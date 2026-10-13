"""Approved B: actual public brokers, synthetic keys and isolated legacy files."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from orqetia.infrastructure import local_oidc as oidc
from orqetia.infrastructure import local_oidc_epoch as epoch
from tests.devenv.test_oidc_marker_retention_safety import (
    ISSUED_AT,
    REJECTIONS,
    Clock,
    Harness,
    Login,
)


@pytest.fixture(params=["backoffice", "portal"])
def harness(request: pytest.FixtureRequest, tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch) -> Harness:
    clock = Clock()
    monkeypatch.setattr(oidc, "time", clock)
    result = Harness(tmp_path / "legacy", str(request.param), clock)
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", result.ledger)
    return result


def resign(harness: Harness, login: Login, field: str, value: object) -> Login:
    payload = oidc._verify(login.transaction_token, harness.key.encode())
    payload[field] = value
    private = oidc._sign(payload, harness.key.encode())
    public = oidc._sign({k: v for k, v in payload.items() if k != "code_verifier"},
                       harness.key.encode())
    code = oidc._verify(login.code, harness.key.encode())
    code["transaction_hash"] = hashlib.sha256(public.encode()).hexdigest()
    return Login(oidc._sign(code, harness.key.encode()), login.state, private,
                 str(code["transaction_hash"]), login.deadline)


@pytest.mark.parametrize("delay", [0, 290])
@pytest.mark.parametrize("after", [0, 1])
def test_inclusive_wall_expiry_and_late_code(harness: Harness, delay: int, after: int) -> None:
    login = harness.issue(delay)
    harness.clock.now = login.deadline + after
    if after:
        with pytest.raises(REJECTIONS, match="expired"):
            harness.complete(login)
    else:
        harness.complete(login)
        with pytest.raises(REJECTIONS, match="already consumed"):
            harness.complete(login)
    assert not harness.ledger.exists()


@pytest.mark.parametrize("consumed", [False, True])
def test_restart_and_restored_disk_cannot_revive_login(
    harness: Harness, consumed: bool,
) -> None:
    login = harness.issue()
    if consumed:
        harness.complete(login)
    harness.ledger.mkdir()
    (harness.ledger / "synthetic-old-snapshot").write_bytes(b"unknown")
    restarted = harness.broker()
    for civil in (login.deadline, ISSUED_AT + 1):
        harness.clock.now = civil
        with pytest.raises(REJECTIONS, match="generation"):
            harness.complete(login, restarted)
    assert (harness.ledger / "synthetic-old-snapshot").read_bytes() == b"unknown"


def test_live_other_verifier_cannot_accept_or_consume_issuers_login(harness: Harness) -> None:
    login = harness.issue()
    with pytest.raises(REJECTIONS, match="generation"):
        harness.complete(login, harness.broker())
    harness.complete(login)


@pytest.mark.parametrize("consumed", [False, True])
@pytest.mark.parametrize("swept", [False, True])
def test_monotonic_expiry_survives_civil_rollback(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, consumed: bool, swept: bool,
) -> None:
    mono = [10.0]
    monkeypatch.setattr(epoch, "time", SimpleNamespace(monotonic=lambda: mono[0]))
    login = harness.issue()
    if consumed:
        harness.complete(login)
    mono[0] += 301.001
    harness.clock.now = login.deadline + 61
    if swept:
        harness.issue()
    harness.clock.now = ISSUED_AT + 1
    with pytest.raises(REJECTIONS, match="monotonic|not issued"):
        harness.complete(login)


def test_replay_before_expiry_survives_civil_rollback(harness: Harness) -> None:
    login = harness.issue()
    harness.complete(login)
    harness.clock.now = login.deadline + 60
    with pytest.raises(REJECTIONS, match="expired"):
        harness.complete(login)
    harness.clock.now = ISSUED_AT + 1
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(login)


def test_exact_monotonic_fence_is_inclusive(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = [0.0]
    monkeypatch.setattr(epoch, "time", SimpleNamespace(monotonic=lambda: mono[0]))
    login = harness.issue()
    mono[0] = 301.0
    harness.clock.now = login.deadline
    harness.complete(login)


def test_concurrent_callbacks_have_one_winner(harness: Harness) -> None:
    login = harness.issue()
    barrier = Barrier(2, timeout=5)

    def attempt() -> bool:
        barrier.wait()
        try:
            harness.complete(login)
            return True
        except REJECTIONS:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]


def test_capacity_and_sweep_bound_apply_to_real_broker(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = [0.0]
    monkeypatch.setattr(epoch, "time", SimpleNamespace(monotonic=lambda: mono[0]))
    first = harness.issue()
    harness.complete(first)
    mono[0] = 0.1  # deterministic expiry order, independently of digest ordering
    for _ in range(4095):
        harness.issue()
    assert harness.active._epoch.stats() == (4096, 4096)
    with pytest.raises(REJECTIONS, match="capacity"):
        harness.issue()
    with pytest.raises(REJECTIONS, match="already consumed"):
        harness.complete(first)
    mono[0] = 302.0
    harness.issue()
    assert harness.active._epoch.stats() == (4096 - 64 + 1,) * 2
    with pytest.raises(REJECTIONS, match="not issued"):
        harness.complete(first)
    assert not harness.ledger.exists()


def test_forked_instance_fails_closed(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    login = harness.issue()
    monkeypatch.setattr(epoch, "os", SimpleNamespace(getpid=lambda: -1))
    with pytest.raises(REJECTIONS, match="process changed"):
        harness.complete(login)
    with pytest.raises(REJECTIONS, match="process changed"):
        harness.issue()


def test_entropy_initialization_failure_has_no_fallback(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_size: int) -> str:
        raise OSError("synthetic entropy failure")

    monkeypatch.setattr(epoch, "secrets", SimpleNamespace(token_urlsafe=unavailable))
    with pytest.raises(OSError, match="entropy"):
        harness.broker()
    assert not harness.ledger.exists()


def test_monotonic_failure_never_authenticates(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = harness.issue()

    def unavailable() -> float:
        raise OSError("synthetic clock failure")

    monkeypatch.setattr(epoch, "time", SimpleNamespace(monotonic=unavailable))
    with pytest.raises(OSError, match="clock"):
        harness.complete(login)
    with pytest.raises(OSError, match="clock"):
        harness.issue()


@pytest.mark.parametrize("field,value", [
    ("generation", "old"), ("generation", None), ("generation", "é"),
    ("state", "other"), ("nonce", "other"), ("code_verifier", "other"),
    ("audience", "other"), ("issuer", "other"), ("callback", "https://other.invalid"),
])
def test_signed_but_invalid_binding_rejected_without_consuming_original(
    harness: Harness, field: str, value: object,
) -> None:
    login = harness.issue()
    with pytest.raises(REJECTIONS):
        harness.complete(resign(harness, login, field, value))
    harness.complete(login)


def test_no_marker_io_and_legacy_metadata_preserved(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.ledger.mkdir()
    marker = harness.ledger / "synthetic-legacy"
    marker.write_bytes(b"unknown-format")
    os.utime(marker, (1, 1))
    (harness.ledger / "invalid-directory").mkdir()
    before = marker.stat()
    login = harness.issue()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("epoch login must perform no legacy file IO")

    with monkeypatch.context() as patch:
        for name in ("open", "scandir", "listdir", "unlink", "remove"):
            patch.setattr(os, name, forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        harness.complete(login)
        with pytest.raises(REJECTIONS, match="already consumed"):
            harness.complete(login)
    assert marker.read_bytes() == b"unknown-format"
    assert marker.stat().st_mtime_ns == before.st_mtime_ns
    assert len(list(harness.ledger.iterdir())) == 2
