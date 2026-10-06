from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
