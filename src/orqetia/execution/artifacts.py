"""Execution-owned client-private artifact contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .sessions import OwnershipScope
from .tasks import TaskPayloadReferences


@dataclass(frozen=True)
class SanitizedEvidenceRecord:
    media_type: str
    sanitized_raw_body: str
    sanitized_sha256: str
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.media_type.strip() or len(self.media_type) > 200:
            raise ValueError("evidence media_type must contain 1..200 characters")
        if len(self.sanitized_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.sanitized_sha256
        ):
            raise ValueError("sanitized_sha256 must be lowercase SHA-256 hex")
        actual = hashlib.sha256(self.sanitized_raw_body.encode("utf-8")).hexdigest()
        if actual != self.sanitized_sha256:
            raise ValueError("sanitized evidence SHA-256 does not match body")


@dataclass(frozen=True)
class ClientExchangeEvidence:
    exchange_id: UUID
    attempt_id: UUID
    provider_id: str
    provider_name: str
    operation: str
    status: str
    status_label: str
    request_evidence: SanitizedEvidenceRecord
    response_evidence: SanitizedEvidenceRecord | None = None

    def __post_init__(self) -> None:
        for field, value, maximum in (
            ("provider_id", self.provider_id, 100),
            ("provider_name", self.provider_name, 200),
            ("operation", self.operation, 100),
            ("status", self.status, 100),
            ("status_label", self.status_label, 200),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must contain 1..{maximum} characters")


class ClientRequestArtifactWriter(Protocol):
    async def store_request(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> TaskPayloadReferences:
        """Persist client-private input and return only durable references."""


class ClientResultArtifactWriter(Protocol):
    async def store_result(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        result: dict[str, object],
        occurred_at: datetime,
    ) -> str:
        """Persist one immutable client-safe terminal result."""


class ClientResultArtifactReader(Protocol):
    async def read_result(
        self,
        *,
        scope: OwnershipScope,
        result_reference: str,
    ) -> dict[str, object] | None:
        """Read a client-safe result by already-authorized owner/reference."""


class ClientExchangeEvidenceWriter(Protocol):
    async def store_exchange(
        self,
        *,
        scope: OwnershipScope,
        evidence: ClientExchangeEvidence,
        occurred_at: datetime,
    ) -> None:
        """Persist only already-sanitized exchange evidence."""


class ClientExchangeEvidenceReader(Protocol):
    async def list_for_attempt(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> tuple[ClientExchangeEvidence, ...]:
        """Return retained sanitized raw evidence only."""


class ClientArtifactRetentionPort(Protocol):
    async def delete_owned_before(
        self,
        *,
        scope: OwnershipScope,
        older_than: datetime,
    ) -> int:
        """Delete owned artifact/evidence content older than the retention cutoff."""
