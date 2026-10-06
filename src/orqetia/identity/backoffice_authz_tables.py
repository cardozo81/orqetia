"""Identity-owned Backoffice external identity bindings."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

backoffice_authz_metadata = metadata_for_schema("identity")

backoffice_user_bindings = sa.Table(
    "backoffice_user_bindings",
    backoffice_authz_metadata,
    sa.Column("binding_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("issuer", sa.Text(), nullable=False),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("roles", JSONB(), nullable=False),
    sa.Column("role_matrix_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("issuer", "subject", name="backoffice_external_identity"),
    sa.CheckConstraint(
        "issuer LIKE 'https://%' AND char_length(issuer) <= 500",
        name="backoffice_issuer_https",
    ),
    sa.CheckConstraint(
        "char_length(subject) BETWEEN 1 AND 500",
        name="backoffice_subject_bounded",
    ),
    sa.CheckConstraint(
        "status IN ('ACTIVE','DISABLED')",
        name="backoffice_binding_status_known",
    ),
    sa.CheckConstraint(
        "jsonb_typeof(roles) = 'array' AND jsonb_array_length(roles) >= 1",
        name="backoffice_roles_non_empty",
    ),
    sa.CheckConstraint(
        "role_matrix_version >= 1 AND version >= 1",
        name="backoffice_versions_positive",
    ),
)

sa.Index(
    "ix_backoffice_user_bindings_status",
    backoffice_user_bindings.c.status,
)
