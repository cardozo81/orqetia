"""Identity-owned tenant and service-client tables."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

tenancy_metadata = metadata_for_schema("identity")

tenants = sa.Table(
    "tenants",
    tenancy_metadata,
    sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.CheckConstraint(
        "char_length(display_name) BETWEEN 1 AND 200",
        name="tenant_display_name_bounded",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="tenant_status_known",
    ),
    sa.CheckConstraint("version >= 1", name="tenant_version_positive"),
)

service_clients = sa.Table(
    "service_clients",
    tenancy_metadata,
    sa.Column("client_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["identity.tenants.tenant_id"],
        name="service_client_tenant",
        ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "char_length(display_name) BETWEEN 1 AND 200",
        name="client_display_name_bounded",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="client_status_known",
    ),
    sa.CheckConstraint("version >= 1", name="client_version_positive"),
)

sa.Index(
    "ix_service_clients_tenant_status",
    service_clients.c.tenant_id,
    service_clients.c.status,
)
