"""Bounded ephemeral one-time LOCAL/TEST OIDC admission, per verifier process.

An old signed transaction is invalid after restart/restore because its random
process generation is not recoverable. This module does not touch on-disk markers.
See issue #174 and docs/security/local-oidc-epoch-invalidations.md.
"""

from __future__ import annotations

import hashlib
import heapq
import hmac
import os
import secrets
import threading
import time


class LocalOidcEpochLedger:
    """Issued/consumed transaction registry; deny on fork or unknown generation."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        limit: int = 4096,
        sweep_budget: int = 64,
    ) -> None:
        if ttl_seconds < 1 or limit < 1 or sweep_budget < 1:
            raise ValueError("invalid LOCAL OIDC ledger limits")
        self._pid = os.getpid()
        self._generation = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, bool]] = {}
        self._expiry: list[tuple[float, str]] = []
        self._ttl = ttl_seconds
        self._limit = limit
        self._sweep_budget = sweep_budget

    @property
    def generation(self) -> str:
        self._check_pid()
        return self._generation

    def _check_pid(self) -> None:
        if self._pid != os.getpid():
            raise PermissionError("LOCAL OIDC verifier process changed")

    def _sweep_locked(self, now: float) -> None:
        """Incremental expiry; consumed and pending records share the same bound."""
        for _ in range(self._sweep_budget):
            if not self._expiry or self._expiry[0][0] >= now:
                break
            deadline, digest = heapq.heappop(self._expiry)
            value = self._entries.get(digest)
            if value is not None and value[0] == deadline:
                del self._entries[digest]

    def issue(self, public_transaction: str) -> None:
        self._check_pid()
        # The wall-clock signed expiry is inclusive in its integer second.
        deadline = time.monotonic() + self._ttl + 1
        digest = hashlib.sha256(public_transaction.encode("utf-8")).hexdigest()
        with self._lock:
            self._check_pid()
            self._sweep_locked(time.monotonic())
            if len(self._entries) >= self._limit:
                raise PermissionError("LOCAL OIDC login capacity exhausted")
            if digest in self._entries:
                raise PermissionError("LOCAL OIDC duplicate transaction")
            self._entries[digest] = (deadline, False)
            heapq.heappush(self._expiry, (deadline, digest))

    def consume(self, public_transaction: str, generation: str) -> None:
        self._check_pid()
        if not hmac.compare_digest(generation, self._generation):
            raise PermissionError("LOCAL OIDC login generation expired")
        digest = hashlib.sha256(public_transaction.encode("utf-8")).hexdigest()
        with self._lock:
            self._check_pid()
            now = time.monotonic()
            record = self._entries.get(digest)
            if record is None:
                raise PermissionError("LOCAL OIDC transaction not issued by this verifier")
            deadline, consumed = record
            if now > deadline:
                raise PermissionError("LOCAL OIDC transaction monotonic deadline expired")
            if consumed:
                raise PermissionError("authorization transaction already consumed")
            self._entries[digest] = (deadline, True)
            self._sweep_locked(now)

    def stats(self) -> tuple[int, int]:
        """Aggregate non-sensitive record counts for controlled tests."""
        self._check_pid()
        with self._lock:
            return len(self._entries), len(self._expiry)
