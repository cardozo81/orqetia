"""#174 delta to #173: a fresh verifier rejects restored old login state."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from orqetia.infrastructure import local_oidc as oidc


@pytest.mark.parametrize("audience", ["backoffice", "portal"])
def test_oidc_ledger_loss_and_restart_require_new_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audience: str,
) -> None:
    now = 1_800_000_000
    monkeypatch.setattr(oidc, "time", SimpleNamespace(time=lambda: now))
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    historical = ledger / "synthetic-historical-marker"
    historical.touch()
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", ledger)
    key = secrets.token_urlsafe(48)

    def broker() -> oidc._LocalBrokerBase:
        return oidc._LocalBrokerBase(
            environment="test", signing_key=key, audience=audience,
            callback_url=f"https://synthetic.invalid/{audience}/callback",
            idp_public_url="https://synthetic.invalid/dev-idp",
        )

    original = broker()
    url, private_transaction = original._start()
    public_transaction = parse_qs(urlsplit(url).query)["transaction"][0]
    payload = oidc._verify(public_transaction, key.encode())
    code = oidc._sign({
        "kind": "authorization_code", "issuer": oidc.LOCAL_OIDC_ISSUER,
        "audience": audience, "subject": "synthetic-recovery-subject",
        "nonce": payload["nonce"],
        "transaction_hash": hashlib.sha256(public_transaction.encode()).hexdigest(),
        "authenticated_at": now, "iat": now, "exp": now + oidc._TOKEN_TTL_SECONDS,
    }, key.encode())
    args = {"code": code, "state": str(payload["state"]),
            "transaction_token": private_transaction}

    assert original._complete(**args).subject == "synthetic-recovery-subject"
    markers = list(ledger.iterdir())
    assert len(markers) == 1 and markers[0].stat().st_size == 0
    with pytest.raises(PermissionError, match="already consumed"):
        original._complete(**args)
    with pytest.raises(PermissionError, match="generation"):
        broker()._complete(**args)

    # Simulate an absent/older snapshot by selecting a new isolated directory.
    missing_ledger = tmp_path / "missing-ledger"
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", missing_ledger)
    now += oidc._TOKEN_TTL_SECONDS
    with pytest.raises(PermissionError, match="generation"):
        broker()._complete(**args)
    assert not missing_ledger.exists()

    # Strictly past the transaction deadline, missing markers cannot revive it.
    now += 1
    expired_ledger = tmp_path / "expired-ledger"
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", expired_ledger)
    with pytest.raises(PermissionError, match="expired"):
        broker()._complete(**args)
    assert not expired_ledger.exists()
    assert len(list(ledger.iterdir())) == 1  # no creation, access or purge of legacy
    assert historical.read_bytes() == b""
