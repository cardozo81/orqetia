"""Physical schema registry and context-local SQLAlchemy metadata."""

from __future__ import annotations

from types import MappingProxyType

from sqlalchemy import MetaData

AUTHORITATIVE_SCHEMAS = (
    "identity",
    "control",
    "execution",
    "accounting",
    "estimation",
    "audit",
    "readmodel",
)

INFRASTRUCTURE_SCHEMAS = ("messaging",)

SCHEMA_OWNERSHIP = MappingProxyType(
    {
        "identity": "identity_tenancy",
        "control": "control_plane",
        "execution": "execution",
        "accounting": "usage_accounting",
        "estimation": "estimation",
        "audit": "audit_operations",
        "readmodel": "read_models",
        "messaging": "infrastructure_messaging",
    }
)

NAMING_CONVENTION = MappingProxyType(
    {
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def metadata_for_schema(schema: str) -> MetaData:
    """Return isolated metadata for one schema owner.

    A separate metadata registry prevents accidental cross-context ORM
    relationships from becoming the default merely because contexts share one
    PostgreSQL database.
    """

    if schema not in SCHEMA_OWNERSHIP:
        raise ValueError(f"unknown ORQETIA schema: {schema}")
    return MetaData(schema=schema, naming_convention=dict(NAMING_CONVENTION))
