from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from orqetia.identity import (
    AuthenticationRejected,
    ClientAccessCredentialService,
    IdempotencyConflict,
    InMemoryClientCredentialStore,
    StoredClientCredentialAuthenticator,
)

NOW = datetime(2026, 10, 5, 23, tzinfo=UTC)
TENANT_ID = UUID("0199b39a-9bf1-7000-8000-000000000020")
CLIENT_ID = UUID("0199b39a-9bf1-7000-8000-000000000021")


@pytest.mark.asyncio
async def test_issue_is_one_time_hashed_and_authenticates_with_safe_principal() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    issued = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="ci",
        scopes=("usage:read", "tasks:write"),
        idempotency_key="issue-1",
        occurred_at=NOW,
    )
    assert issued.secret is not None
    secret = issued.secret.reveal_once()
    with pytest.raises(RuntimeError, match="already revealed"):
        issued.secret.reveal_once()

    safe = issued.credential.safe_view()
    assert "secret_hash" not in safe
    assert "secret_salt" not in safe
    assert secret not in repr(issued)
    assert secret not in str(issued.secret)

    authenticator = StoredClientCredentialAuthenticator(
        store=store,
        now=lambda: NOW + timedelta(seconds=1),
    )
    principal = await authenticator.authenticate_bearer(secret)
    assert principal.tenant_id == str(TENANT_ID)
    assert principal.client_id == str(CLIENT_ID)
    assert principal.credential_id == str(issued.credential.credential_id)
    assert principal.credential_fingerprint == issued.credential.fingerprint
    assert principal.scopes == frozenset({"usage:read", "tasks:write"})

    persisted = await store.get(issued.credential.credential_id)
    assert persisted is not None
    assert persisted.last_used_at == NOW + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_issue_idempotency_replays_without_revealing_secret_again() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    first = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="integration",
        scopes=("usage:read",),
        idempotency_key="same-key",
        occurred_at=NOW,
    )
    replay = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="integration",
        scopes=("usage:read",),
        idempotency_key="same-key",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert replay.replayed
    assert replay.secret is None
    assert replay.credential.credential_id == first.credential.credential_id

    with pytest.raises(IdempotencyConflict):
        await service.issue(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            display_label="different",
            scopes=("usage:read",),
            idempotency_key="same-key",
            occurred_at=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_rotate_invalidates_old_secret_and_replay_does_not_rotate_twice() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    issued = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="rotating",
        scopes=("usage:read",),
        idempotency_key="issue-rotate",
        occurred_at=NOW,
    )
    assert issued.secret is not None
    old_secret = issued.secret.reveal_once()

    rotated = await service.rotate(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        credential_id=issued.credential.credential_id,
        idempotency_key="rotate-1",
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert rotated.secret is not None
    new_secret = rotated.secret.reveal_once()
    assert rotated.credential.key_version == 2

    authenticator = StoredClientCredentialAuthenticator(
        store=store,
        now=lambda: NOW + timedelta(minutes=2),
    )
    with pytest.raises(AuthenticationRejected):
        await authenticator.authenticate_bearer(old_secret)
    assert (await authenticator.authenticate_bearer(new_secret)).credential_id == str(
        issued.credential.credential_id
    )

    replay = await service.rotate(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        credential_id=issued.credential.credential_id,
        idempotency_key="rotate-1",
        occurred_at=NOW + timedelta(minutes=3),
    )
    assert replay.replayed
    assert replay.secret is None
    assert replay.credential.key_version == 2


@pytest.mark.asyncio
async def test_revoke_is_effective_and_ownership_is_fail_closed() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    issued = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="revocable",
        scopes=("usage:read",),
        idempotency_key="issue-revoke",
        occurred_at=NOW,
    )
    assert issued.secret is not None
    token = issued.secret.reveal_once()

    with pytest.raises(PermissionError, match="ownership mismatch"):
        await service.rotate(
            tenant_id=TENANT_ID,
            client_id=UUID("0199b39a-9bf1-7000-8000-000000000099"),
            credential_id=issued.credential.credential_id,
            idempotency_key="wrong-owner",
            occurred_at=NOW + timedelta(seconds=1),
        )

    revoked = await service.revoke(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        credential_id=issued.credential.credential_id,
        idempotency_key="revoke-1",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert revoked.credential.status.value == "REVOKED"
    authenticator = StoredClientCredentialAuthenticator(
        store=store,
        now=lambda: NOW + timedelta(seconds=3),
    )
    with pytest.raises(AuthenticationRejected):
        await authenticator.authenticate_bearer(token)


@pytest.mark.asyncio
async def test_expired_credential_is_rejected() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    issued = await service.issue(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        display_label="expiring",
        scopes=("usage:read",),
        idempotency_key="issue-expire",
        occurred_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
    )
    assert issued.secret is not None
    token = issued.secret.reveal_once()
    authenticator = StoredClientCredentialAuthenticator(
        store=store,
        now=lambda: NOW + timedelta(seconds=6),
    )
    with pytest.raises(AuthenticationRejected):
        await authenticator.authenticate_bearer(token)
