"""#174 safety evidence, not a collector: all deletion is synthetic tmp_path only.

Tests named counterexample intentionally demonstrate why the candidate must not
ship. The signed-horizon helper is a test model, never used by runtime admission.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier
from urllib.parse import parse_qs, urlsplit

import pytest

from orqetia.identity import HumanAuthenticationContext
from orqetia.infrastructure import local_oidc as oidc
from orqetia.infrastructure.backoffice import OidcLoginRejected
from orqetia.infrastructure.customer_portal.app import CustomerPortalOidcRejected

Broker = oidc.LocalBackofficeOidcBroker | oidc.LocalCustomerPortalOidcBroker
REJECTIONS = (OidcLoginRejected, CustomerPortalOidcRejected)
ISSUED_AT = 1_800_000_000


@dataclass
class Clock:
    now: int = ISSUED_AT

    def time(self) -> float:
        return float(self.now)


@dataclass(repr=False)
class Login:
    code: str
    state: str
    transaction_token: str
    digest: str
    deadline: int


@dataclass
class Harness:
    ledger: Path
    audience: str
    clock: Clock
    key: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)

    def broker(self, environment: str = "test") -> Broker:
        cls = (oidc.LocalBackofficeOidcBroker if self.audience == "backoffice"
               else oidc.LocalCustomerPortalOidcBroker)
        return cls(
            environment=environment, signing_key=self.key, audience=self.audience,
            callback_url=f"https://synthetic.invalid/{self.audience}/callback",
            idp_public_url="https://synthetic.invalid/dev-idp",
        )

    def issue(self, code_delay: int = 0) -> Login:
        async def begin() -> tuple[str, str]:
            start = await self.broker().begin_login()
            return start.authorization_url, start.transaction_token

        url, private_transaction = asyncio.run(begin())
        public = parse_qs(urlsplit(url).query)["transaction"][0]
        payload = oidc._verify(public, self.key.encode())
        deadline = payload["exp"]
        assert type(deadline) is int
        digest = hashlib.sha256(public.encode()).hexdigest()
        self.clock.now += code_delay
        code = oidc._sign({
            "v": 1, "kind": "authorization_code", "issuer": oidc.LOCAL_OIDC_ISSUER,
            "audience": self.audience, "subject": "synthetic-retention-subject",
            "nonce": payload["nonce"], "transaction_hash": digest,
            "iat": self.clock.now, "authenticated_at": self.clock.now,
            "exp": self.clock.now + oidc._TOKEN_TTL_SECONDS,
        }, self.key.encode())
        return Login(code, str(payload["state"]), private_transaction, digest, deadline)

    def complete(self, login: Login, broker: Broker | None = None) -> HumanAuthenticationContext:
        return asyncio.run((broker or self.broker()).complete_login(
            code=login.code, state=login.state, transaction_token=login.transaction_token,
        ))

    def marker(self, login: Login) -> Path:
        return self.ledger / login.digest


@pytest.fixture(params=["backoffice", "portal"])
def harness(request: pytest.FixtureRequest, tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch) -> Harness:
    clock = Clock()
    result = Harness(tmp_path / "ledger", str(request.param), clock)
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", result.ledger)
    # Replace this module's clock, not asyncio's or the process-global time module.
    monkeypatch.setattr(oidc, "time", clock)
    return result


@pytest.mark.parametrize("code_delay", [0, 290])
@pytest.mark.parametrize("after_deadline", [0, 1])
def test_effective_transaction_deadline_is_inclusive(
    harness: Harness, code_delay: int, after_deadline: int,
) -> None:
    login = harness.issue(code_delay)
    harness.clock.now = login.deadline + after_deadline
    if after_deadline:
        with pytest.raises(REJECTIONS, match="expired"):
            harness.complete(login)
        assert not harness.ledger.exists()
    else:
        assert harness.complete(login).subject == "synthetic-retention-subject"
        with pytest.raises(REJECTIONS, match="already consumed"):
            harness.complete(login)


def test_intact_ledger_rejects_replay_after_restart_and_clock_rollback(harness: Harness) -> None:
    login = harness.issue()
    harness.complete(login)
    harness.clock.now = login.deadline + 1
    with pytest.raises(REJECTIONS, match="expired"):
        harness.complete(login)
    harness.clock.now = ISSUED_AT + 1
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(login, harness.broker())
    assert harness.marker(login).exists()


def test_two_concurrent_brokers_have_exactly_one_atomic_winner(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = harness.issue()
    barrier = Barrier(2, timeout=5)
    original_open = os.open
    creation_flags: list[int] = []

    def synchronized_open(path: str | Path, flags: int, mode: int = 0o777) -> int:
        if Path(path) == harness.marker(login):
            creation_flags.append(flags)
            barrier.wait()
        return original_open(path, flags, mode)

    def attempt() -> bool:
        try:
            harness.complete(login, harness.broker())
            return True
        except REJECTIONS:
            return False

    monkeypatch.setattr(os, "open", synchronized_open)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == [False, True]
    assert len(creation_flags) == 2
    assert all(flags & os.O_CREAT and flags & os.O_EXCL for flags in creation_flags)
    assert len(list(harness.ledger.iterdir())) == 1


@pytest.mark.parametrize("restart", [False, True])
def test_counterexample_expired_marker_deletion_then_clock_rollback_revives_code(
    harness: Harness, restart: bool,
) -> None:
    login = harness.issue()
    broker = harness.broker()
    harness.complete(login, broker)
    harness.clock.now = login.deadline + 61  # even the proposed extra margin elapsed
    with pytest.raises(REJECTIONS, match="expired"):
        harness.complete(login, broker)
    harness.marker(login).unlink()  # simulate unsafe GC, only inside tmp_path
    harness.clock.now = ISSUED_AT + 1
    # This observed success is the counterexample, never an accepted GC invariant.
    assert harness.complete(login, harness.broker() if restart else broker).subject == (
        "synthetic-retention-subject"
    )


@pytest.mark.parametrize("mtime", [1, ISSUED_AT + 86_400])
@pytest.mark.parametrize("contents", [b"", b"unknown-version", b'{"exp":0}'])
def test_restored_or_unknown_marker_metadata_cannot_authorize_reuse(
    harness: Harness, mtime: int, contents: bytes,
) -> None:
    login = harness.issue()
    harness.complete(login)
    marker = harness.marker(login)
    marker.write_bytes(contents)  # synthetic legacy/unknown restored metadata
    os.utime(marker, (mtime, mtime))
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(login)
    assert marker.read_bytes() == contents
    assert int(marker.stat().st_mtime) == mtime


def test_directory_in_place_of_marker_is_preserved_and_rejected(harness: Harness) -> None:
    login = harness.issue()
    marker = harness.marker(login)
    marker.mkdir(parents=True)
    # Windows reports EACCES, Linux EEXIST; both must deny without touching it.
    with pytest.raises(REJECTIONS):
        harness.complete(login)
    assert marker.is_dir()


def test_marker_symlink_does_not_modify_target(harness: Harness, tmp_path: Path) -> None:
    login = harness.issue()
    harness.ledger.mkdir()
    target = tmp_path / "synthetic-sentinel"
    target.write_bytes(b"unchanged")
    try:
        harness.marker(login).symlink_to(target)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows symlink privilege unavailable; Linux CI exercises this case")
        raise
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(login)
    assert target.read_bytes() == b"unchanged"
    assert harness.marker(login).is_symlink()


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.ENOSPC, errno.EIO])
def test_marker_creation_failure_never_returns_authenticated_context(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, error_number: int,
) -> None:
    login = harness.issue()

    def fail_open(path: str | Path, flags: int, mode: int = 0o777) -> int:
        raise OSError(error_number, "synthetic filesystem fault")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises((*REJECTIONS, OSError)):
        harness.complete(login)
    assert not harness.marker(login).exists()


def test_close_failure_leaves_conservative_marker(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = harness.issue()
    original_close = os.close

    def fail_after_close(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError(errno.EIO, "synthetic close fault")

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", fail_after_close)
        with pytest.raises(OSError):
            harness.complete(login)
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(login)


def test_ledger_directory_creation_failure_denies(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = harness.issue()

    def fail_mkdir(self: Path, mode: int = 0o777,
                   parents: bool = False, exist_ok: bool = False) -> None:
        raise PermissionError("synthetic ledger inaccessible")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    with pytest.raises(REJECTIONS):
        harness.complete(login)


def test_preserved_callback_does_no_directory_scan_or_deletion(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.ledger.mkdir()
    for index in range(150):
        (harness.ledger / hashlib.sha256(str(index).encode()).hexdigest()).touch()
    login = harness.issue()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("baseline callback must not enumerate or delete ledger entries")

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", forbidden)
        patch.setattr(os, "listdir", forbidden)
        patch.setattr(os, "unlink", forbidden)
        patch.setattr(os, "remove", forbidden)
        assert harness.complete(login).subject == "synthetic-retention-subject"
    assert len(list(harness.ledger.iterdir())) == 151


def _model_with_signed_horizon(
    harness: Harness, login: Login, horizon_file: Path,
) -> HumanAuthenticationContext:
    """Candidate-model only: assumes a trusted, non-rollback horizon authority."""
    try:
        raw = horizon_file.read_text(encoding="utf-8")
        record = oidc._verify(raw, harness.key.encode())
        horizon = record.get("horizon")
        if record.get("kind") != "retention-model-v1" or type(horizon) is not int:
            raise ValueError("invalid horizon")
    except (OSError, ValueError) as error:
        raise PermissionError("model horizon unavailable") from error
    if login.deadline <= horizon:
        raise PermissionError("model horizon rejects transaction")
    return harness.complete(login, harness.broker())


def test_counterexample_restored_authentic_horizon_cannot_prove_freshness(
    harness: Harness, tmp_path: Path,
) -> None:
    login = harness.issue()
    horizon_file = tmp_path / "synthetic-horizon"
    old_snapshot = oidc._sign({"kind": "retention-model-v1", "horizon": ISSUED_AT - 1},
                              harness.key.encode())
    horizon_file.write_text(old_snapshot, encoding="utf-8")
    _model_with_signed_horizon(harness, login, horizon_file)
    harness.clock.now = login.deadline + 61
    horizon_file.write_text(oidc._sign(
        {"kind": "retention-model-v1", "horizon": login.deadline}, harness.key.encode(),
    ), encoding="utf-8")
    harness.marker(login).unlink()  # simulate proposed purge, only synthetic state
    harness.clock.now = ISSUED_AT + 1
    with pytest.raises(PermissionError, match="horizon rejects"):
        _model_with_signed_horizon(harness, login, horizon_file)

    # Same key and valid signature; changing mtime does not establish freshness.
    horizon_file.write_text(old_snapshot, encoding="utf-8")
    os.utime(horizon_file, (harness.clock.now, harness.clock.now))
    assert oidc._verify(horizon_file.read_text(), harness.key.encode())["horizon"] == ISSUED_AT - 1
    assert _model_with_signed_horizon(harness, login, horizon_file).subject == (
        "synthetic-retention-subject"
    )


@pytest.mark.parametrize("metadata", [None, "", "invalid.signature"])
def test_candidate_horizon_model_denies_missing_or_invalid_state(
    harness: Harness, tmp_path: Path, metadata: str | None,
) -> None:
    login = harness.issue()
    horizon_file = tmp_path / "synthetic-horizon"
    if metadata is not None:
        horizon_file.write_text(metadata, encoding="utf-8")
    with pytest.raises(PermissionError, match="unavailable"):
        _model_with_signed_horizon(harness, login, horizon_file)
    assert not harness.marker(login).exists()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_retention_analysis_does_not_extend_local_auth_to_other_environments(
    harness: Harness, environment: str,
) -> None:
    with pytest.raises(RuntimeError, match="LOCAL/TEST"):
        harness.broker(environment)
