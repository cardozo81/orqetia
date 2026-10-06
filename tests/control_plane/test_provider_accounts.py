from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid7

import pytest

from orqetia.control_plane import (
    ExternalCapacitySource,
    InMemoryExternalCapacityRepository,
    InMemoryProviderAccountRepository,
    InMemoryProviderCredentialRepository,
    ProviderAccountService,
    ProviderAccountStatus,
    ProviderCredentialMetadata,
    ProviderCredentialStatus,
    SecretReference,
)
from orqetia.execution import (
    ExecutionTargetSnapshot,
    OwnershipScope,
    ProviderAttempt,
    ProviderAttemptStatus,
)
from orqetia.usage_accounting import AccountingDimensions

NOW = datetime(2026, 10, 5, 22, tzinfo=UTC)


def _credential(
    *,
    account_id,
    provider_id: str = "openai",
    status: ProviderCredentialStatus = ProviderCredentialStatus.ACTIVE,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    failed_at: datetime | None = None,
) -> ProviderCredentialMetadata:
    return ProviderCredentialMetadata(
        credential_id=uuid7(),
        provider_id=provider_id,
        provider_account_id=account_id,
        secret_reference=SecretReference(f"memory://{uuid7()}"),
        fingerprint="0123456789abcdef",
        key_version=1,
        state_version=1,
        status=status,
        created_at=created_at,
        expires_at=expires_at,
        last_failed_use_at=failed_at,
        revoked_at=NOW if status is ProviderCredentialStatus.REVOKED else None,
    )


@pytest.mark.asyncio
async def test_select_credential_respects_account_priority_status_and_expiry() -> None:
    accounts = InMemoryProviderAccountRepository()
    credentials = InMemoryProviderCredentialRepository()
    capacity = InMemoryExternalCapacityRepository()
    service = ProviderAccountService(
        accounts=accounts,
        credentials=credentials,
        capacity=capacity,
    )

    preferred = await service.create_account(
        provider_id="openai",
        display_label="primary",
        occurred_at=NOW,
        priority=10,
        region="us",
    )
    fallback = await service.create_account(
        provider_id="openai",
        display_label="secondary",
        occurred_at=NOW,
        priority=20,
        region="us",
    )
    expired = _credential(
        account_id=preferred.provider_account_id,
        expires_at=NOW - timedelta(seconds=1),
    )
    fallback_credential = _credential(account_id=fallback.provider_account_id)
    await credentials.create(expired)
    await credentials.create(fallback_credential)

    selected = await service.select_credential(
        provider_id="openai",
        occurred_at=NOW,
        region="us",
    )
    assert selected.provider_account_id == fallback.provider_account_id
    assert selected.provider_credential_id == fallback_credential.credential_id

    await service.set_status(
        provider_account_id=fallback.provider_account_id,
        status=ProviderAccountStatus.DISABLED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(LookupError, match="no active provider credential"):
        await service.select_credential(
            provider_id="openai",
            occurred_at=NOW + timedelta(seconds=2),
            region="us",
        )


@pytest.mark.asyncio
async def test_capacity_is_recorded_only_from_explicit_provider_observation() -> None:
    accounts = InMemoryProviderAccountRepository()
    capacity = InMemoryExternalCapacityRepository()
    service = ProviderAccountService(
        accounts=accounts,
        credentials=InMemoryProviderCredentialRepository(),
        capacity=capacity,
    )
    account = await service.create_account(
        provider_id="openai",
        display_label="primary",
        occurred_at=NOW,
    )

    snapshot = await service.record_external_capacity(
        provider_account_id=account.provider_account_id,
        native_unit="CREDIT",
        source=ExternalCapacitySource.PROVIDER_API,
        source_reference="provider://balance",
        observed_at=NOW,
        remaining=Decimal("42.5"),
        limit=Decimal("100"),
    )
    assert snapshot.remaining == Decimal("42.5")
    assert (
        await capacity.latest(
            provider_account_id=account.provider_account_id,
            native_unit="CREDIT",
        )
        == snapshot
    )

    with pytest.raises(ValueError, match="requires provider-observed"):
        await service.record_external_capacity(
            provider_account_id=account.provider_account_id,
            native_unit="CREDIT",
            source=ExternalCapacitySource.PROVIDER_API,
            source_reference="provider://missing",
            observed_at=NOW,
        )


def test_attempt_and_accounting_provenance_require_account_for_credential() -> None:
    credential_id = uuid7()
    with pytest.raises(ValueError, match="requires provider account"):
        ProviderAttempt(
            attempt_id=uuid7(),
            ownership=OwnershipScope(tenant_id=uuid7(), client_id=uuid7()),
            operation="TASK_EXECUTION",
            target=ExecutionTargetSnapshot("openai", "gpt-x", "medium"),
            cycle=1,
            attempt_index=1,
            request_reference="memory://request",
            request_fingerprint="a" * 64,
            status=ProviderAttemptStatus.PREPARED,
            created_at=NOW,
            updated_at=NOW,
            provider_credential_id=credential_id,
        )

    with pytest.raises(ValueError, match="requires provider account"):
        AccountingDimensions(
            tenant_id=uuid7(),
            client_id=uuid7(),
            attempt_id=uuid7(),
            provider_id="openai",
            model_id="gpt-x",
            reasoning_profile="medium",
            provider_credential_id=credential_id,
        )
