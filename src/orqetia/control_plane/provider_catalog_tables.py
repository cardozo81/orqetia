"""Control-plane storage for immutable provider catalog versions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

provider_catalog_metadata = metadata_for_schema("control")

provider_catalog_versions = sa.Table(
    "provider_catalog_versions",
    provider_catalog_metadata,
    sa.Column("catalog_version_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("version_number", sa.Integer(), nullable=False, unique=True),
    sa.Column("catalog", JSONB(), nullable=False),
    sa.Column("endpoints", JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "version_number >= 1",
        name="provider_catalog_version_positive",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(catalog) = 'array'",
        name="provider_catalog_array",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(endpoints) = 'array'",
        name="provider_catalog_endpoints_array",
    ),
)

provider_catalog_assignment = sa.Table(
    "provider_catalog_assignment",
    provider_catalog_metadata,
    sa.Column("assignment_key", sa.Text(), primary_key=True),
    sa.Column("catalog_version_id", UUID(as_uuid=True), nullable=False),
    sa.Column("assignment_version", sa.Integer(), nullable=False),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["catalog_version_id"],
        ["control.provider_catalog_versions.catalog_version_id"],
        name="fk_provider_catalog_assignment_version",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "assignment_key = 'GLOBAL'",
        name="provider_catalog_assignment_singleton",
    ),
    sa.CheckConstraint(
        "assignment_version >= 1",
        name="provider_catalog_assignment_version_positive",
    ),
)
