"""Identity-owned server-side Backoffice browser sessions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from orqetia.infrastructure.persistence.schemas import metadata_for_schema

web_session_metadata = metadata_for_schema("identity")

backoffice_web_sessions = sa.Table(
    "backoffice_web_sessions",
    web_session_metadata,
    sa.Column("session_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("csrf_hash", sa.String(64), nullable=False),
    sa.Column("issuer", sa.Text(), nullable=False),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("mfa_satisfied", sa.Boolean(), nullable=False),
    sa.Column("amr", JSONB(), nullable=False),
    sa.Column("acr", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="web_session_token_hash"),
    sa.CheckConstraint("csrf_hash ~ '^[0-9a-f]{64}$'", name="web_session_csrf_hash"),
    sa.CheckConstraint(
        "jsonb_typeof(amr) = 'array'",
        name="web_session_amr_array",
    ),
    sa.CheckConstraint(
        "absolute_expires_at > created_at AND last_activity_at >= created_at",
        name="web_session_time_order",
    ),
    sa.CheckConstraint("version >= 1", name="web_session_version_positive"),
)

sa.Index(
    "ix_backoffice_web_sessions_identity",
    backoffice_web_sessions.c.issuer,
    backoffice_web_sessions.c.subject,
)
sa.Index(
    "ix_backoffice_web_sessions_expiry",
    backoffice_web_sessions.c.absolute_expires_at,
)
