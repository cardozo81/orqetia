from __future__ import annotations

import json
import os

import pytest

from orqetia.infrastructure import local_bootstrap as bootstrap
from orqetia.infrastructure.local_secrets import LocalFileProviderSecretStore
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_does_not_log_raw_tokens(
    tmp_path, monkeypatch, caplog, capsys
):
    if "ORQETIA_DATABASE_DSN" not in os.environ:
        pytest.skip("isolated PostgreSQL test database is required")
    for name, relative in (
        ("_TOKEN_PATH", "tokens.json"),
        ("_MANIFEST_PATH", "manifest.json"),
        ("_AUDIT_PATH", "audit.jsonl"),
        ("LOCAL_OIDC_SIGNING_KEY_PATH", "signing.key"),
        ("LOCAL_LOGIN_TOKENS_PATH", "login.json"),
    ):
        monkeypatch.setattr(bootstrap, name, tmp_path / relative)
    engine = create_engine(RuntimeSettings())
    try:
        sessions = create_session_factory(engine)
        store = LocalFileProviderSecretStore(tmp_path / "secrets", environment="test")
        await bootstrap.bootstrap_local(sessions, secret_store=store, environment="test")
        first = json.loads((tmp_path / "manifest.json").read_text())
        tokens = (tmp_path / "tokens.json").read_text()
        logins = (tmp_path / "login.json").read_text()
        await bootstrap.bootstrap_local(sessions, secret_store=store, environment="test")
        second = json.loads((tmp_path / "manifest.json").read_text())
        for section in ("tenants", "clients", "memberships", "identities", "provider"):
            assert first[section] == second[section]
        assert tokens == (tmp_path / "tokens.json").read_text()
        assert logins == (tmp_path / "login.json").read_text()
        # Reusing an existing provider performs no provider mutation, so it
        # legitimately creates no new provider audit file on repeat suite runs.
        audit_path = tmp_path / "audit.jsonl"
        captured = capsys.readouterr()
        audit = audit_path.read_text() if audit_path.exists() else ""
        audit += caplog.text + captured.out + captured.err
        assert all(token not in audit for token in json.loads(tokens).values())
        assert all(token not in audit for token in json.loads(logins).values())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["staging", "production"])
async def test_bootstrap_rejects_nonlocal_before_database_or_filesystem(environment):
    with pytest.raises(RuntimeError, match="LOCAL/TEST"):
        await bootstrap.bootstrap_local(None, secret_store=None, environment=environment)
