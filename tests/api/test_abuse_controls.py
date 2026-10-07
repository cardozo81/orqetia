from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx
import pytest

from orqetia.identity import AuthenticatedPrincipal
from orqetia.infrastructure.http import create_app
from orqetia.infrastructure.http.abuse import (
    AbuseRule,
    ApiAbusePolicy,
    ApiRouteClass,
    InMemoryApiAbuseController,
    TechnicalAbuseRejected,
    classify_route,
    validate_request_body,
)

NOW = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)


def _policy() -> ApiAbusePolicy:
    rule = AbuseRule(
        max_body_bytes=64,
        max_json_depth=3,
        max_json_nodes=8,
        requests_per_window=2,
        window_seconds=60,
    )
    return ApiAbusePolicy(
        max_concurrent_requests=1,
        queue_ready_depth_limit=2,
        queue_retry_after_seconds=7,
        read=rule,
        polling=rule,
        estimate=rule,
        mutation=rule,
        sensitive_mutation=rule,
    )


def test_route_classification_distinguishes_sensitive_and_polling() -> None:
    assert classify_route("POST", "/v1/credentials") is ApiRouteClass.SENSITIVE_MUTATION
    assert classify_route("POST", "/v1/estimates") is ApiRouteClass.ESTIMATE
    assert classify_route("GET", "/v1/tasks/abc") is ApiRouteClass.POLLING
    assert classify_route("GET", "/v1/providers") is ApiRouteClass.READ
    assert classify_route("GET", "/health/live") is None


def test_body_and_complexity_limits_fail_closed() -> None:
    rule = _policy().mutation
    with pytest.raises(TechnicalAbuseRejected) as oversized:
        validate_request_body(
            rule=rule,
            body=b"x" * 65,
            content_type="application/octet-stream",
        )
    assert oversized.value.status_code == 413

    deep = b'{"a":{"b":{"c":{"d":1}}}}'
    with pytest.raises(TechnicalAbuseRejected) as complex_error:
        validate_request_body(
            rule=rule,
            body=deep,
            content_type="application/json",
        )
    assert complex_error.value.status_code == 422
    assert complex_error.value.code == "REQUEST_COMPLEXITY_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_concurrency_and_queue_backpressure_are_bounded() -> None:
    controller = InMemoryApiAbuseController(_policy())

    await controller.check_rate(
        identity_key="tenant-a/client-a",
        route_class=ApiRouteClass.READ,
        occurred_at=NOW,
    )
    await controller.check_rate(
        identity_key="tenant-a/client-a",
        route_class=ApiRouteClass.READ,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(TechnicalAbuseRejected) as limited:
        await controller.check_rate(
            identity_key="tenant-a/client-a",
            route_class=ApiRouteClass.READ,
            occurred_at=NOW + timedelta(seconds=2),
        )
    assert limited.value.status_code == 429
    assert limited.value.retry_after_seconds == 58

    await controller.acquire_request()
    with pytest.raises(TechnicalAbuseRejected) as saturated:
        await controller.acquire_request()
    assert saturated.value.code == "API_BACKPRESSURE"
    await controller.release_request()

    with pytest.raises(TechnicalAbuseRejected) as queue:
        await controller.check_queue_depth(ready_depth=2)
    assert queue.value.code == "QUEUE_BACKPRESSURE"
    assert queue.value.retry_after_seconds == 7

    snapshot = await controller.saturation_snapshot()
    assert snapshot.active_requests == 0
    assert snapshot.rate_limited_total == 1
    assert snapshot.concurrency_rejected_total == 1
    assert snapshot.queue_rejected_total == 1
    assert "tenant-a" not in repr(snapshot)


class _Authenticator:
    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        assert token == "synthetic"
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="synthetic",
            tenant_id=str(uuid7()),
            client_id=str(uuid7()),
            scopes=frozenset({"catalog:read", "estimates:write"}),
        )


async def _request(
    app,
    method: str,
    path: str,
    *,
    content: bytes | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer synthetic"}
    if content is not None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(
            method,
            path,
            headers=headers,
            content=content,
        )


@pytest.mark.asyncio
async def test_http_abuse_middleware_returns_sanitized_body_and_complexity_errors() -> None:
    controller = InMemoryApiAbuseController(_policy())
    app = create_app(
        openapi_document={"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}},
        authenticator=_Authenticator(),
        abuse_controller=controller,
    )

    oversized = await _request(
        app,
        "POST",
        "/v1/estimates",
        content=b"x" * 65,
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "REQUEST_BODY_TOO_LARGE"
    assert "65" not in oversized.text

    complex_payload = b'{"a":{"b":{"c":{"d":1}}}}'
    complex_response = await _request(
        app,
        "POST",
        "/v1/estimates",
        content=complex_payload,
    )
    assert complex_response.status_code == 422
    assert complex_response.json()["code"] == "REQUEST_COMPLEXITY_EXCEEDED"


@pytest.mark.asyncio
async def test_http_rate_limit_emits_retry_after_without_identity_leak() -> None:
    rule = AbuseRule(
        max_body_bytes=64,
        max_json_depth=3,
        max_json_nodes=8,
        requests_per_window=1,
        window_seconds=60,
    )
    controller = InMemoryApiAbuseController(
        ApiAbusePolicy(
            max_concurrent_requests=2,
            queue_ready_depth_limit=10,
            queue_retry_after_seconds=5,
            read=rule,
            polling=rule,
            estimate=rule,
            mutation=rule,
            sensitive_mutation=rule,
        )
    )
    app = create_app(
        openapi_document={"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}},
        authenticator=_Authenticator(),
        abuse_controller=controller,
    )

    first = await _request(app, "GET", "/v1/providers")
    assert first.status_code == 503

    second = await _request(app, "GET", "/v1/providers")
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert int(second.headers["retry-after"]) >= 1
    assert "synthetic" not in second.text
