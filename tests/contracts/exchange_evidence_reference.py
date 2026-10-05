"""Test-only oracle for sanitized exchange evidence and provider identity.

Origin requirement: RASAi PR #201 / main 8008a3e24550f7c4b199beb0adeefa9c4e618538.
No RASAi runtime/code dependency exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from html import escape, unescape


@dataclass(frozen=True)
class ProviderIdentity:
    provider_id: str
    provider_name: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id required")
        if not self.provider_name.strip():
            raise ValueError("provider_name required")


@dataclass(frozen=True)
class SanitizedRawEvidence:
    body: str
    media_type: str
    sha256_hex: str
    truncated: bool = False


@dataclass(frozen=True)
class ExchangeRecord:
    exchange_id: str
    attempt_id: str
    provider: ProviderIdentity
    operation: str
    status: str
    request: SanitizedRawEvidence
    response: SanitizedRawEvidence | None


def sanitize_for_persistence(raw: str, *, secrets: tuple[str, ...] = ()) -> SanitizedRawEvidence:
    sanitized = raw
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    digest = sha256(sanitized.encode("utf-8")).hexdigest()
    return SanitizedRawEvidence(
        body=sanitized,
        media_type="application/json",
        sha256_hex=digest,
    )


_STATUS_LABELS = {
    "SUCCEEDED": "Succeeded",
    "FAILED_KNOWN": "Failed",
}


def humanize_status(status: str) -> str:
    return _STATUS_LABELS.get(status, "Unknown status")


def raw_api_dto(exchange: ExchangeRecord) -> dict[str, object]:
    return {
        "attempt_id": exchange.attempt_id,
        "exchange_id": exchange.exchange_id,
        "provider": {
            "provider_id": exchange.provider.provider_id,
            "provider_name": exchange.provider.provider_name,
        },
        "metadata": {
            "operation": exchange.operation,
            "status": exchange.status,
            "status_label": humanize_status(exchange.status),
        },
        "request_evidence": {
            "media_type": exchange.request.media_type,
            "sanitized_raw_body": exchange.request.body,
            "sanitized_sha256": exchange.request.sha256_hex,
            "truncated": exchange.request.truncated,
        },
        "response_evidence": None
        if exchange.response is None
        else {
            "media_type": exchange.response.media_type,
            "sanitized_raw_body": exchange.response.body,
            "sanitized_sha256": exchange.response.sha256_hex,
            "truncated": exchange.response.truncated,
        },
    }


def html_transport(raw_evidence: SanitizedRawEvidence) -> str:
    return escape(raw_evidence.body)


def recover_html_transport(rendered: str) -> str:
    return unescape(rendered)


def provider_display(provider: ProviderIdentity) -> str:
    """Provider-specific display boundary; never generic status humanization."""
    return provider.provider_name or provider.provider_id


def forbidden_client_fields(dto: Mapping[str, object]) -> bool:
    forbidden = {
        "provider_secret",
        "provider_credential_id",
        "provider_cost",
        "currency",
        "credit_balance",
    }

    def walk(value: object) -> bool:
        if isinstance(value, Mapping):
            if any(str(key) in forbidden for key in value):
                return True
            return any(walk(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(walk(item) for item in value)
        return False

    return walk(dto)
