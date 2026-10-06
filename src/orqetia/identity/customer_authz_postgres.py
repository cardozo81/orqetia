"""PostgreSQL repository for customer-human identities and memberships."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .customer_authz import (
    CustomerIdentity,
    CustomerIdentityStatus,
    CustomerMembership,
    CustomerMembershipStatus,
    CustomerRole,
)
from .customer_authz_tables import customer_identities, customer_memberships

SessionFactory = async_sessionmaker[AsyncSession]


def _identity_values(identity: CustomerIdentity) -> dict[str, object]:
    return {
        "identity_id": identity.identity_id,
        "issuer": identity.issuer,
        "subject": identity.subject,
        "status": identity.status.value,
        "created_at": identity.created_at,
        "updated_at": identity.updated_at,
        "version": identity.version,
    }


def _membership_values(membership: CustomerMembership) -> dict[str, object]:
    return {
        "membership_id": membership.membership_id,
        "identity_id": membership.identity_id,
        "tenant_id": membership.tenant_id,
        "client_id": membership.client_id,
        "roles": [role.value for role in membership.roles],
        "status": membership.status.value,
        "role_matrix_version": membership.role_matrix_version,
        "created_at": membership.created_at,
        "updated_at": membership.updated_at,
        "version": membership.version,
    }


def _identity_from_row(row: RowMapping) -> CustomerIdentity:
    return CustomerIdentity(
        identity_id=cast(UUID, row["identity_id"]),
        issuer=cast(str, row["issuer"]),
        subject=cast(str, row["subject"]),
        status=CustomerIdentityStatus(cast(str, row["status"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=cast(int, row["version"]),
    )


def _membership_from_row(row: RowMapping) -> CustomerMembership:
    raw_roles = row["roles"]
    if not isinstance(raw_roles, list):
        raise ValueError("persisted customer roles must be an array")
    return CustomerMembership(
        membership_id=cast(UUID, row["membership_id"]),
        identity_id=cast(UUID, row["identity_id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        client_id=cast(UUID, row["client_id"]),
        roles=tuple(CustomerRole(str(value)) for value in raw_roles),
        status=CustomerMembershipStatus(cast(str, row["status"])),
        role_matrix_version=cast(int, row["role_matrix_version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=cast(int, row["version"]),
    )


class PostgresCustomerIdentityRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def create_identity(self, identity: CustomerIdentity) -> CustomerIdentity:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(customer_identities).values(**_identity_values(identity))
            )
        return identity

    async def get_identity(self, identity_id: UUID) -> CustomerIdentity | None:
        statement = sa.select(customer_identities).where(
            customer_identities.c.identity_id == identity_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _identity_from_row(row)

    async def get_by_external_identity(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> CustomerIdentity | None:
        statement = sa.select(customer_identities).where(
            customer_identities.c.issuer == issuer.strip(),
            customer_identities.c.subject == subject.strip(),
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _identity_from_row(row)

    async def replace_identity(
        self,
        identity: CustomerIdentity,
        *,
        expected_version: int,
    ) -> CustomerIdentity:
        values = _identity_values(identity)
        values.pop("identity_id")
        values.pop("issuer")
        values.pop("subject")
        statement = (
            sa.update(customer_identities)
            .where(
                customer_identities.c.identity_id == identity.identity_id,
                customer_identities.c.issuer == identity.issuer,
                customer_identities.c.subject == identity.subject,
                customer_identities.c.version == expected_version,
            )
            .values(**values)
            .returning(customer_identities.c.identity_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("customer identity version conflict")
        return identity

    async def create_membership(
        self,
        membership: CustomerMembership,
    ) -> CustomerMembership:
        async with self._sessions.begin() as database:
            await database.execute(
                sa.insert(customer_memberships).values(
                    **_membership_values(membership)
                )
            )
        return membership

    async def get_membership(
        self,
        membership_id: UUID,
    ) -> CustomerMembership | None:
        statement = sa.select(customer_memberships).where(
            customer_memberships.c.membership_id == membership_id
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _membership_from_row(row)

    async def find_membership(
        self,
        *,
        identity_id: UUID,
        tenant_id: UUID,
        client_id: UUID,
    ) -> CustomerMembership | None:
        statement = sa.select(customer_memberships).where(
            customer_memberships.c.identity_id == identity_id,
            customer_memberships.c.tenant_id == tenant_id,
            customer_memberships.c.client_id == client_id,
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        return None if row is None else _membership_from_row(row)

    async def list_memberships(
        self,
        *,
        identity_id: UUID,
    ) -> tuple[CustomerMembership, ...]:
        statement = (
            sa.select(customer_memberships)
            .where(customer_memberships.c.identity_id == identity_id)
            .order_by(
                customer_memberships.c.tenant_id,
                customer_memberships.c.client_id,
                customer_memberships.c.membership_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()
        return tuple(_membership_from_row(row) for row in rows)

    async def replace_membership(
        self,
        membership: CustomerMembership,
        *,
        expected_version: int,
    ) -> CustomerMembership:
        values = _membership_values(membership)
        for immutable in ("membership_id", "identity_id", "tenant_id", "client_id"):
            values.pop(immutable)
        statement = (
            sa.update(customer_memberships)
            .where(
                customer_memberships.c.membership_id == membership.membership_id,
                customer_memberships.c.identity_id == membership.identity_id,
                customer_memberships.c.tenant_id == membership.tenant_id,
                customer_memberships.c.client_id == membership.client_id,
                customer_memberships.c.version == expected_version,
            )
            .values(**values)
            .returning(customer_memberships.c.membership_id)
        )
        async with self._sessions.begin() as database:
            changed = (await database.execute(statement)).scalar_one_or_none()
        if changed is None:
            raise ValueError("customer membership version/ownership conflict")
        return membership
