"""Secure, rebuildable Web Read Model contracts."""

from .cache import CacheLookup, InMemoryReadModelCache
from .domain import (
    SURFACE_POLICIES,
    ProjectionFreshness,
    ProjectionIdentity,
    ReadAudience,
    ReadClassification,
    ReadModelDocument,
    ReadSurface,
    SurfacePolicy,
)
from .pagination import CursorPage, decode_cursor, encode_cursor, paginate
from .store import PostgresReadModelStore, ReadModelStore
from .tables import projection_documents

__all__ = [
    "CacheLookup",
    "CursorPage",
    "InMemoryReadModelCache",
    "PostgresReadModelStore",
    "ProjectionFreshness",
    "ProjectionIdentity",
    "ReadAudience",
    "ReadClassification",
    "ReadModelDocument",
    "ReadModelStore",
    "ReadSurface",
    "SURFACE_POLICIES",
    "SurfacePolicy",
    "decode_cursor",
    "encode_cursor",
    "paginate",
    "projection_documents",
]
