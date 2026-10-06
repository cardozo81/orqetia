"""Identity-owned customer human identities and client memberships."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

customer_authz_metadata = metadata_for_schema("identity")

customer_identities = sa.Table(
    "customer_identities",
    customer_authz_metadata,
    sa.Column("identity_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("issuer", sa.Text(), nullable=False),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("issuer", "subject", name="customer_external_identity"),
    sa.CheckConstraint(
        "issuer LIKE 'https://%' AND char_length(issuer) <= 500",
        name="customer_issuer_https",
    ),
    sa.CheckConstraint(
        "char_length(subject) BETWEEN 1 AND 500",
        name="customer_subject_bounded",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="customer_identity_status_known",
    ),
    sa.CheckConstraint("version >= 1", name="customer_identity_version_positive"),
)

customer_memberships = sa.Table(
    "customer_memberships",
    customer_authz_metadata,
    sa.Column("membership_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("identity_id", UUID(as_uuid=True), nullable=False),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), nullable=False),
    sa.Column("roles", JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("role_matrix_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(
        ["identity_id"],
        ["identity.customer_identities.identity_id"],
        name="customer_membership_identity",
        ondelete="RESTRICT",
    ),
    sa.UniqueConstraint(
        "identity_id",
        "tenant_id",
        "client_id",
        name="customer_membership_owner",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(roles) = 'array' AND jsonb_array_length(roles) >= 1",
        name="customer_membership_roles_non_empty",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="customer_membership_status_known",
    ),
    sa.CheckConstraint(
        "role_matrix_version >= 1 AND version >= 1",
        name="customer_membership_versions_positive",
    ),
)

sa.Index(
    "ix_customer_memberships_identity_status",
    customer_memberships.c.identity_id,
    customer_memberships.c.status,
)
sa.Index(
    "ix_customer_memberships_owner",
    customer_memberships.c.tenant_id,
    customer_memberships.c.client_id,
)
