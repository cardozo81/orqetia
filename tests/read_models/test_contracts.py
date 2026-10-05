from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from orqetia.read_models import (
    InMemoryReadModelCache,
    ProjectionFreshness,
    ProjectionIdentity,
    ReadAudience,
    ReadClassification,
    ReadModelDocument,
    ReadSurface,
    decode_cursor,
    paginate,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)


def _identity(
    *,
    tenant_id=None,
    client_id=None,
    role_scope=("client_reader",),
    surface=ReadSurface.USAGE_SUMMARY,
) -> ProjectionIdentity:
    return ProjectionIdentity(
        surface=surface,
        audience=ReadAudience.CLIENT,
        tenant_id=tenant_id or uuid7(),
        client_id=client_id or uuid7(),
        role_scope=role_scope,
        query_fingerprint="period=2026-10",
    )


def _document(identity: ProjectionIdentity, *, payload=None) -> ReadModelDocument:
    return ReadModelDocument.create(
        identity=identity,
        version=1,
        classification=ReadClassification.CLIENT_PRIVATE,
        freshness=ProjectionFreshness(
            as_of=NOW,
            source_watermark="usage-ledger:100",
            refreshed_at=NOW,
        ),
        payload=payload or {"total_tokens": 1234, "requests": 10},
    )


def test_cache_partition_includes_owner_role_surface_and_query() -> None:
    tenant, client = uuid7(), uuid7()
    first = _identity(tenant_id=tenant, client_id=client, role_scope=("reader",))
    different_role = _identity(
        tenant_id=tenant,
        client_id=client,
        role_scope=("admin",),
    )
    different_client = _identity(tenant_id=tenant, client_id=uuid7())
    assert first.cache_partition_key != different_role.cache_partition_key
    assert first.cache_partition_key != different_client.cache_partition_key


def test_client_projection_rejects_financial_fields_recursively() -> None:
    with pytest.raises(ValueError, match="forbidden financial field"):
        _document(
            _identity(),
            payload={"usage": {"provider_cost": "1.23", "total_tokens": 10}},
        )


def test_backoffice_financial_projection_is_allowed() -> None:
    identity = ProjectionIdentity(
        surface=ReadSurface.PROVIDER_COST_SUMMARY,
        audience=ReadAudience.BACKOFFICE,
        role_scope=("finance_admin",),
        query_fingerprint="period=2026-10",
    )
    document = ReadModelDocument.create(
        identity=identity,
        version=1,
        classification=ReadClassification.CONFIDENTIAL,
        freshness=ProjectionFreshness(
            as_of=NOW,
            source_watermark="accounting:200",
            refreshed_at=NOW,
        ),
        payload={"provider_cost": "12.50", "currency": "USD"},
    )
    assert document.payload["currency"] == "USD"


def test_stale_while_revalidate_only_on_allowed_surface() -> None:
    cache = InMemoryReadModelCache()
    usage = _document(_identity(surface=ReadSurface.USAGE_SUMMARY))
    cache.put(usage)
    stale_usage = cache.get(
        identity=usage.identity,
        now=NOW + timedelta(seconds=90),
    )
    assert stale_usage.hit and stale_usage.stale

    quota = _document(_identity(surface=ReadSurface.QUOTA_UTILIZATION))
    cache.put(quota)
    stale_quota = cache.get(
        identity=quota.identity,
        now=NOW + timedelta(seconds=6),
    )
    assert not stale_quota.hit


def test_cache_invalidation_is_owner_scoped() -> None:
    cache = InMemoryReadModelCache()
    tenant = uuid7()
    first = _document(_identity(tenant_id=tenant, client_id=uuid7()))
    second = _document(_identity(tenant_id=tenant, client_id=uuid7()))
    other = _document(_identity(tenant_id=uuid7(), client_id=uuid7()))
    for document in (first, second, other):
        cache.put(document)
    assert cache.invalidate(tenant_id=tenant) == 2
    assert cache.get(identity=other.identity, now=NOW).hit


def test_cursor_pagination_is_query_bound_and_not_authorization_state() -> None:
    items = tuple(f"{index:03d}" for index in range(12))
    first = paginate(
        items,
        key=lambda item: item,
        page_size=5,
        query_fingerprint="tenant-owned-query",
    )
    assert first.items == ("000", "001", "002", "003", "004")
    assert first.next_cursor is not None
    assert (
        decode_cursor(
            cursor=first.next_cursor,
            query_fingerprint="tenant-owned-query",
        )
        == "004"
    )
    second = paginate(
        items,
        key=lambda item: item,
        page_size=5,
        query_fingerprint="tenant-owned-query",
        cursor=first.next_cursor,
    )
    assert second.items == ("005", "006", "007", "008", "009")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        decode_cursor(
            cursor=first.next_cursor,
            query_fingerprint="different-query",
        )


def test_client_http_cache_control_is_private_and_bounded() -> None:
    document = _document(_identity(surface=ReadSurface.OVERVIEW))
    assert document.cache_control() == "private, max-age=30, stale-while-revalidate=60"
