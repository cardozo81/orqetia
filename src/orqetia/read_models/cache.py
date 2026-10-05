"""Owner/role-partitioned server-side cache for rebuildable read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .domain import ProjectionIdentity, ReadModelDocument, SURFACE_POLICIES


@dataclass(frozen=True)
class CacheLookup:
    document: ReadModelDocument | None
    stale: bool = False

    @property
    def hit(self) -> bool:
        return self.document is not None


class InMemoryReadModelCache:
    """Deterministic reference cache. Cache state never grants write authorization."""

    def __init__(self) -> None:
        self._entries: dict[str, ReadModelDocument] = {}

    def put(self, document: ReadModelDocument) -> None:
        self._entries[document.identity.cache_partition_key] = document

    def get(self, *, identity: ProjectionIdentity, now: datetime) -> CacheLookup:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        key = identity.cache_partition_key
        document = self._entries.get(key)
        if document is None:
            return CacheLookup(None)

        stale_at = document.freshness.stale_at(identity.surface)
        expires_at = document.freshness.expires_at(identity.surface)
        if now >= expires_at:
            self._entries.pop(key, None)
            return CacheLookup(None)
        if now >= stale_at:
            if not SURFACE_POLICIES[identity.surface].stale_while_revalidate:
                self._entries.pop(key, None)
                return CacheLookup(None)
            return CacheLookup(document, stale=True)
        return CacheLookup(document, stale=False)

    def invalidate(
        self,
        *,
        tenant_id: UUID | None = None,
        client_id: UUID | None = None,
        surface: object | None = None,
    ) -> int:
        removed = 0
        for key, document in tuple(self._entries.items()):
            identity = document.identity
            if tenant_id is not None and identity.tenant_id != tenant_id:
                continue
            if client_id is not None and identity.client_id != client_id:
                continue
            if surface is not None and identity.surface != surface:
                continue
            self._entries.pop(key, None)
            removed += 1
        return removed
