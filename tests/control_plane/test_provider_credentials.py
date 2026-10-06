from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest

from orqetia.control_plane import (
    CredentialPreflightResult,
    InMemoryCredentialAuditSink,
    InMemoryProviderCredentialRepository,
    InMemoryProviderSecretStore,
    ProviderCredentialMetadata,
    ProviderCredentialService,
    ProviderCredentialStatus,
    SecretReference,
    SecretValue,
)

NOW = datetime(2026, 10, 5, 21, tzinfo=UTC)
RAW_SECRET = "provider-secret-must-never-leak"


class FakePreflight:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.seen: list[tuple[str, str]] = []

    async def check(
        self,
        *,
        provider_id: str,
        secret: SecretValue,
    ) -> CredentialPreflightResult:
        self.seen.append((provider_id, secret.reveal()))
        return CredentialPreflightResult(self.ok, "OK" if self.ok else "AUTH_FAILED")


class FailingReplaceRepository(InMemoryProviderCredentialRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

    async def replace(
        self,
        metadata: ProviderCredentialMetadata,
        *,
        expected_state_version: int,
    ) -> ProviderCredentialMetadata:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("synthetic persistence failure")
        return await super().replace(
            metadata,
            expected_state_version=expected_state_version,
        )


def _service(
    *,
    repository: InMemoryProviderCredentialRepository | None = None,
    preflight: FakePreflight | None = None,
):
    secrets = InMemoryProviderSecretStore()
    repo = repository or InMemoryProviderCredentialRepository()
    audit = InMemoryCredentialAuditSink()
    service = ProviderCredentialService(
        secrets=secrets,
        repository=repo,
        audit=audit,
        preflight=preflight,
    )
    return service, secrets, repo, audit


@pytest.mark.asyncio
async def test_create_persists_only_safe_metadata_and_redacts_secret_object() -> None:
    service, secrets, _, audit = _service()
    secret = SecretValue(RAW_SECRET)
    created = await service.create(
        provider_id="openai",
        provider_account_id=uuid7(),
        secret=secret,
        occurred_at=NOW,
    )

    assert str(secret) == "[REDACTED]"
    assert RAW_SECRET not in repr(secret)
    assert RAW_SECRET not in repr(created)
    safe = created.safe_view()
    assert "secret_reference" not in safe
    assert RAW_SECRET not in repr(safe)
    assert (await secrets.get(created.secret_reference)).reveal() == RAW_SECRET
    assert audit.events[-1].action == "CREATE"
    assert RAW_SECRET not in repr(audit.events)


@pytest.mark.asyncio
async def test_rotation_commits_new_reference_before_retiring_old_secret() -> None:
    service, secrets, _, audit = _service()
    created = await service.create(
        provider_id="anthropic",
        provider_account_id=uuid7(),
        secret=SecretValue("old-value"),
        occurred_at=NOW,
    )
    old_reference = created.secret_reference

    rotated = await service.rotate(
        credential_id=created.credential_id,
        new_secret=SecretValue("new-value"),
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert rotated.key_version == 2
    assert rotated.state_version == 2
    assert rotated.fingerprint != created.fingerprint
    assert rotated.secret_reference != old_reference
    assert (await secrets.get(rotated.secret_reference)).reveal() == "new-value"
    with pytest.raises(LookupError):
        await secrets.get(old_reference)
    assert audit.events[-1].action == "ROTATE"


@pytest.mark.asyncio
async def test_rotation_persistence_failure_preserves_old_secret_and_metadata() -> None:
    repository = FailingReplaceRepository()
    service, secrets, _, _ = _service(repository=repository)
    created = await service.create(
        provider_id="openai",
        provider_account_id=uuid7(),
        secret=SecretValue("stable-old"),
        occurred_at=NOW,
    )
    repository.fail_next = True

    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        await service.rotate(
            credential_id=created.credential_id,
            new_secret=SecretValue("discard-new"),
            occurred_at=NOW + timedelta(minutes=1),
        )

    persisted = await repository.get(created.credential_id)
    assert persisted == created
    assert (await secrets.get(created.secret_reference)).reveal() == "stable-old"


@pytest.mark.asyncio
async def test_revoke_is_effective_before_secret_cleanup_and_blocks_future_use() -> None:
    preflight = FakePreflight()
    service, secrets, repository, audit = _service(preflight=preflight)
    created = await service.create(
        provider_id="openai",
        provider_account_id=uuid7(),
        secret=SecretValue("revocable"),
        occurred_at=NOW,
    )

    revoked = await service.revoke(
        credential_id=created.credential_id,
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert revoked.status is ProviderCredentialStatus.REVOKED
    assert revoked.revoked_at is not None
    assert revoked.state_version == 2
    assert (await repository.get(created.credential_id)) == revoked
    with pytest.raises(LookupError):
        await secrets.get(created.secret_reference)
    with pytest.raises(ValueError, match="not active"):
        await service.preflight(
            credential_id=created.credential_id,
            occurred_at=NOW + timedelta(minutes=2),
        )
    assert audit.events[-1].action == "REVOKE"


@pytest.mark.asyncio
async def test_preflight_uses_secret_in_memory_only_and_updates_safe_metadata() -> None:
    preflight = FakePreflight(ok=True)
    service, _, repository, audit = _service(preflight=preflight)
    created = await service.create(
        provider_id="mistral",
        provider_account_id=uuid7(),
        secret=SecretValue("preflight-value"),
        occurred_at=NOW,
    )

    result = await service.preflight(
        credential_id=created.credential_id,
        occurred_at=NOW + timedelta(minutes=1),
    )
    persisted = await repository.get(created.credential_id)

    assert result.ok
    assert preflight.seen == [("mistral", "preflight-value")]
    assert persisted is not None
    assert persisted.last_successful_use_at == NOW + timedelta(minutes=1)
    assert persisted.state_version == 2
    assert "preflight-value" not in repr(persisted.safe_view())
    assert "preflight-value" not in repr(audit.events)


def test_secret_reference_requires_opaque_scheme_and_safe_view_is_non_secret() -> None:
    with pytest.raises(ValueError, match="opaque"):
        SecretReference("plaintext-key")

    metadata = ProviderCredentialMetadata(
        credential_id=uuid7(),
        provider_id="openai",
        provider_account_id=UUID("0199b39a-9bf1-7000-8000-000000000001"),
        secret_reference=SecretReference("managed://credential/ref"),
        fingerprint="0123456789abcdef",
        key_version=1,
        state_version=1,
        status=ProviderCredentialStatus.ACTIVE,
        created_at=NOW,
    )
    assert "secret_reference" not in metadata.safe_view()
