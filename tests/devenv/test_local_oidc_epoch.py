"""Directed evidence for the #174 approved LOCAL/TEST epoch primitive.

The currently deployed broker has not yet been migrated to this primitive.
No operational volumes, credentials, services or OIDC markers are accessed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from orqetia.infrastructure import local_oidc_epoch as epoch


def test_single_use_and_fresh_generation_after_restart() -> None:
    first = epoch.LocalOidcEpochLedger()
    first.issue("synthetic signed transaction")
    first.consume("synthetic signed transaction", first.generation)
    with pytest.raises(PermissionError, match="already consumed"):
        first.consume("synthetic signed transaction", first.generation)

    restarted = epoch.LocalOidcEpochLedger()
    assert restarted.generation != first.generation
    with pytest.raises(PermissionError, match="generation expired"):
        restarted.consume("synthetic signed transaction", first.generation)


def test_monotonic_expiry_rejects_after_wall_clock_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = [0.0]
    monkeypatch.setattr(epoch.time, "monotonic", lambda: monotonic[0])
    ledger = epoch.LocalOidcEpochLedger(ttl_seconds=2, sweep_budget=4)
    ledger.issue("consumed")
    ledger.consume("consumed", ledger.generation)

    monotonic[0] = 4.1
    ledger.issue("new")
    assert ledger.stats()[0] == 1
    monotonic[0] = 0.2
    with pytest.raises(PermissionError, match="not issued"):
        ledger.consume("consumed", ledger.generation)


def test_capacity_is_fail_closed_and_sweep_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = [0.0]
    monkeypatch.setattr(epoch.time, "monotonic", lambda: monotonic[0])
    ledger = epoch.LocalOidcEpochLedger(ttl_seconds=1, limit=3, sweep_budget=2)
    for index in range(3):
        ledger.issue(str(index))
    with pytest.raises(PermissionError, match="capacity"):
        ledger.issue("over-capacity")
    monotonic[0] = 3.0
    ledger.issue("first-fresh")
    assert ledger.stats()[0] == 2
    ledger.issue("second-fresh")
    assert ledger.stats()[0] == 2


def test_concurrent_consumers_have_exactly_one_winner() -> None:
    ledger = epoch.LocalOidcEpochLedger()
    ledger.issue("synthetic")

    def attempt(_index: int) -> bool:
        try:
            ledger.consume("synthetic", ledger.generation)
            return True
        except PermissionError:
            return False

    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(attempt, range(8)))
    assert outcomes.count(True) == 1


def test_forked_broker_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = epoch.LocalOidcEpochLedger()
    ledger.issue("synthetic")
    monkeypatch.setattr(epoch.os, "getpid", lambda: -1)
    with pytest.raises(PermissionError, match="process changed"):
        ledger.consume("synthetic", "unknown")
    with pytest.raises(PermissionError, match="process changed"):
        ledger.issue("new")
    with pytest.raises(PermissionError, match="process changed"):
        _ = ledger.generation


@pytest.mark.parametrize("invalid", [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_invalid_limits_deny_construction(invalid: tuple[int, int, int]) -> None:
    ttl_seconds, limit, sweep_budget = invalid
    with pytest.raises(ValueError, match="invalid LOCAL OIDC"):
        epoch.LocalOidcEpochLedger(
            ttl_seconds=ttl_seconds, limit=limit, sweep_budget=sweep_budget
        )
