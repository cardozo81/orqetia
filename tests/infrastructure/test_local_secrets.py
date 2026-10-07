from __future__ import annotations

from pathlib import Path

import pytest

from orqetia.control_plane import SecretReference, SecretValue
from orqetia.infrastructure.local_secrets import LocalFileProviderSecretStore


@pytest.mark.asyncio
async def test_local_file_secret_store_round_trip_and_delete(tmp_path: Path) -> None:
    store = LocalFileProviderSecretStore(tmp_path, environment="local")
    reference = await store.put(SecretValue("synthetic-provider-secret"))

    assert reference.value.startswith("local-file://")
    assert "synthetic-provider-secret" not in reference.value
    assert (await store.get(reference)).reveal() == "synthetic-provider-secret"

    await store.delete(reference)
    with pytest.raises(LookupError, match="not found"):
        await store.get(reference)


def test_local_file_secret_store_rejects_non_local_environment(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="LOCAL/TEST"):
        LocalFileProviderSecretStore(tmp_path, environment="production")


@pytest.mark.asyncio
async def test_local_file_secret_store_rejects_foreign_reference(tmp_path: Path) -> None:
    store = LocalFileProviderSecretStore(tmp_path, environment="test")
    with pytest.raises(LookupError, match="not owned"):
        await store.get(SecretReference("managed://credential-1"))
