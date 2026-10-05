"""PostgreSQL/SQLAlchemy infrastructure foundation."""

from .database import create_engine, create_session_factory, transaction_scope
from .schemas import (
    AUTHORITATIVE_SCHEMAS,
    INFRASTRUCTURE_SCHEMAS,
    SCHEMA_OWNERSHIP,
    metadata_for_schema,
)

__all__ = [
    "AUTHORITATIVE_SCHEMAS",
    "INFRASTRUCTURE_SCHEMAS",
    "SCHEMA_OWNERSHIP",
    "create_engine",
    "create_session_factory",
    "metadata_for_schema",
    "transaction_scope",
]
