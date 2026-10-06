"""Technical API abuse controls, independent from business quotas."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from orqetia.shared.messaging import QueueName


class ApiRouteClass(StrEnum):
    READ = "READ"
    POLLING = "POLLING"
    ESTIMATE = "ESTIMATE"
    MUTATION = "MUTATION"
    SENSITIVE_MUTATION = "SENSITIVE_MUTATION"


@dataclass(frozen=True)
class AbuseRule:
    max_body_bytes: int
    max_json_depth: int
    max_json_nodes: int
    requests_per_window: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.max_body_bytes < 0:
            raise ValueError("max_body_bytes cannot be negative")
        if self.max_json_depth < 0 or self.max_json_nodes < 0:
            raise ValueError("JSON complexity limits cannot be negative")
        if self.requests_per_window < 1:
            raise ValueError("requests_per_window must be positive")
        if not 1 <= self.window_seconds <= 3600:
            raise ValueError("window_seconds must be between 1 and 3600")


@dataclass(frozen=True)
class ApiAbusePolicy:
    max_concurrent_requests: int
    queue_ready_depth_limit: int
    queue_retry_after_seconds: int
    read: AbuseRule
    polling: AbuseRule
    estimate: AbuseRule
    mutation: AbuseRule
    sensitive_mutation: AbuseRule

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent_requests <= 10_000:
            raise ValueError("max_concurrent_requests outside allowed range")
        if not 1 <= self.queue_ready_depth_limit <= 1_000_000:
            raise ValueError("queue_ready_depth_limit outside allowed range")
        if not 1 <= self.queue_retry_after_seconds <= 3600:
            raise ValueError("queue_retry_after_seconds outside allowed range")

    def rule_for(self, route_class: ApiRouteClass) -> AbuseRule:
        return {
            ApiRouteClass.READ: self.read,
            ApiRouteClass.POLLING: self.polling,
            ApiRouteClass.ESTIMATE: self.estimate,
            ApiRouteClass.MUTATION: self.mutation,
            ApiRouteClass.SENSITIVE_MUTATION: self.sensitive_mutation,
        }[route_class]


DEFAULT_API_ABUSE_POLICY = ApiAbusePolicy(
    max_concurrent_requests=128,
    queue_ready_depth_limit=10_000,
    queue_retry_after_seconds=5,
    read=AbuseRule(
        max_body_bytes=0,
        max_json_depth=0,
        max_json_nodes=0,
        requests_per_window=240,
        window_seconds=60,
    ),
    polling=AbuseRule(
        max_body_bytes=0,
        max_json_depth=0,
        max_json_nodes=0,
        requests_per_window=120,
        window_seconds=60,
    ),
    estimate=AbuseRule(
        max_body_bytes=256 * 1024,
        max_json_depth=20,
        max_json_nodes=5_000,
        requests_per_window=30,
        window_seconds=60,
    ),
    mutation=AbuseRule(
        max_body_bytes=256 * 1024,
        max_json_depth=20,
        max_json_nodes=5_000,
        requests_per_window=60,
        window_seconds=60,
    ),
    sensitive_mutation=AbuseRule(
        max_body_bytes=32 * 1024,
        max_json_depth=12,
        max_json_nodes=1_000,
        requests_per_window=20,
        window_seconds=60,
    ),
)


class TechnicalAbuseRejected(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ApiSaturationSnapshot:
    active_requests: int
    rate_limited_total: int
    concurrency_rejected_total: int
    queue_rejected_total: int


class QueueBackpressureProbe(Protocol):
    async def ready_depth(self, queue_name: QueueName) -> int: ...


def classify_route(method: str, path: str) -> ApiRouteClass | None:
    if not path.startswith("/v1/"):
        return None
    upper = method.upper()
    if upper == "GET":
        if path.startswith("/v1/tasks/"):
            return ApiRouteClass.POLLING
        return ApiRouteClass.READ
    if path == "/v1/estimates":
        return ApiRouteClass.ESTIMATE
    if path.startswith("/v1/credentials"):
        return ApiRouteClass.SENSITIVE_MUTATION
    return ApiRouteClass.MUTATION


def validate_request_body(
    *,
    rule: AbuseRule,
    body: bytes,
    content_type: str,
) -> None:
    if len(body) > rule.max_body_bytes:
        raise TechnicalAbuseRejected(
            status_code=413,
            code="REQUEST_BODY_TOO_LARGE",
        )
    if not body or "application/json" not in content_type.lower():
        return
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    except RecursionError as error:
        raise TechnicalAbuseRejected(
            status_code=422,
            code="REQUEST_COMPLEXITY_EXCEEDED",
        ) from error
    depth, nodes = _json_complexity(value)
    if depth > rule.max_json_depth or nodes > rule.max_json_nodes:
        raise TechnicalAbuseRejected(
            status_code=422,
            code="REQUEST_COMPLEXITY_EXCEEDED",
        )


def _json_complexity(value: object) -> tuple[int, int]:
    maximum_depth = 0
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        maximum_depth = max(maximum_depth, depth)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return maximum_depth, nodes


class InMemoryApiAbuseController:
    """Process-local technical limiter; keys are hashed before storage."""

    def __init__(
        self,
        policy: ApiAbusePolicy = DEFAULT_API_ABUSE_POLICY,
    ) -> None:
        self.policy = policy
        self._lock = asyncio.Lock()
        self._active_requests = 0
        self._windows: dict[tuple[str, ApiRouteClass], tuple[int, int]] = {}
        self._rate_limited_total = 0
        self._concurrency_rejected_total = 0
        self._queue_rejected_total = 0

    async def acquire_request(self) -> None:
        async with self._lock:
            if self._active_requests >= self.policy.max_concurrent_requests:
                self._concurrency_rejected_total += 1
                raise TechnicalAbuseRejected(
                    status_code=429,
                    code="API_BACKPRESSURE",
                    retry_after_seconds=1,
                )
            self._active_requests += 1

    async def release_request(self) -> None:
        async with self._lock:
            if self._active_requests > 0:
                self._active_requests -= 1

    async def check_rate(
        self,
        *,
        identity_key: str,
        route_class: ApiRouteClass,
        occurred_at: datetime,
    ) -> None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        rule = self.policy.rule_for(route_class)
        window = int(occurred_at.timestamp()) // rule.window_seconds
        hashed = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
        key = (hashed, route_class)
        async with self._lock:
            current_window, count = self._windows.get(key, (window, 0))
            if current_window != window:
                current_window, count = window, 0
            if count >= rule.requests_per_window:
                self._rate_limited_total += 1
                elapsed = int(occurred_at.timestamp()) % rule.window_seconds
                retry_after = max(1, rule.window_seconds - elapsed)
                raise TechnicalAbuseRejected(
                    status_code=429,
                    code="RATE_LIMITED",
                    retry_after_seconds=retry_after,
                )
            self._windows[key] = (current_window, count + 1)

    async def check_queue_depth(self, *, ready_depth: int) -> None:
        if ready_depth < 0:
            raise ValueError("ready_depth cannot be negative")
        if ready_depth < self.policy.queue_ready_depth_limit:
            return
        async with self._lock:
            self._queue_rejected_total += 1
        raise TechnicalAbuseRejected(
            status_code=429,
            code="QUEUE_BACKPRESSURE",
            retry_after_seconds=self.policy.queue_retry_after_seconds,
        )

    async def saturation_snapshot(self) -> ApiSaturationSnapshot:
        async with self._lock:
            return ApiSaturationSnapshot(
                active_requests=self._active_requests,
                rate_limited_total=self._rate_limited_total,
                concurrency_rejected_total=self._concurrency_rejected_total,
                queue_rejected_total=self._queue_rejected_total,
            )
