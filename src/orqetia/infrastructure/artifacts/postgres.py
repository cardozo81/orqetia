"""PostgreSQL adapter for client-private request/result/evidence artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orqetia.execution import (
    ClientExchangeEvidence,
    OwnershipScope,
    SanitizedEvidenceRecord,
    TaskPayloadReferences,
    client_artifacts,
    client_exchange_evidence,
    provider_attempts,
)
from orqetia.execution.retention import ArtifactRetentionCutoffs
from orqetia.providers import (
    OutputKind,
    ProviderInvocationPayload,
    ProviderTarget,
    StructuredOutputSpec,
)

SessionFactory = async_sessionmaker[AsyncSession]

_REQUEST_MAX_BYTES = 1_048_576
_RESULT_MAX_BYTES = 2_097_152
_PROVIDER_RESPONSE_MAX_BYTES = 2_097_152
_EVIDENCE_MAX_BYTES = 262_144
_REFERENCE_PREFIX = "orqetia-artifact://"


class ArtifactConflict(ValueError):
    """An immutable artifact identity was reused for different content."""


class ArtifactNotFound(LookupError):
    """A durable artifact is absent, deleted or outside its authorized boundary."""


class ArtifactTooLarge(ValueError):
    """Artifact content exceeds the explicit durable-storage bound."""


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_document(value: dict[str, object]) -> tuple[str, str, int]:
    encoded_text = _canonical_json(value)
    encoded = encoded_text.encode("utf-8")
    return encoded_text, hashlib.sha256(encoded).hexdigest(), len(encoded)


def _text_digest(value: str) -> tuple[str, int]:
    encoded = value.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _bounded(size: int, maximum: int, field: str) -> None:
    if size > maximum:
        raise ArtifactTooLarge(f"{field} exceeds {maximum} UTF-8 bytes")


def _reference(artifact_id: UUID) -> str:
    return f"{_REFERENCE_PREFIX}{artifact_id}"


def _artifact_id(reference: str) -> UUID:
    if not reference.startswith(_REFERENCE_PREFIX):
        raise ArtifactNotFound("artifact reference is not owned by this adapter")
    try:
        return UUID(reference.removeprefix(_REFERENCE_PREFIX))
    except ValueError as exc:
        raise ArtifactNotFound("artifact reference is invalid") from exc


def _request_id(scope: OwnershipScope, task_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orqetia:request:{scope.tenant_id}:{scope.client_id}:{task_id}",
    )


def _result_id(scope: OwnershipScope, task_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orqetia:result:{scope.tenant_id}:{scope.client_id}:{task_id}",
    )


def _provider_response_id(attempt_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"orqetia:provider-response:{attempt_id}")


def _structured_output(payload: dict[str, object]) -> StructuredOutputSpec | None:
    raw = payload.get("structured_output")
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    schema = raw.get("schema")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("structured_output.name must be a non-empty string")
    if isinstance(schema, dict):
        schema_json = _canonical_json(schema)
    elif isinstance(schema, str):
        schema_json = schema
    else:
        raise ValueError("structured_output.schema must be an object or JSON string")
    return StructuredOutputSpec(name=name, schema_json=schema_json)


class PostgresClientArtifactStore:
    """One PostgreSQL adapter implementing the client/provider artifact ports.

    Provider request loading intentionally follows the existing provider port contract:
    the orchestration layer must first load the owned task/attempt, then pass its durable
    reference here. The adapter independently verifies the immutable content fingerprint.
    Client-facing result/evidence reads always require tenant/client ownership.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def store_request(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> TaskPayloadReferences:
        _aware(occurred_at, "occurred_at")
        _, fingerprint, size = _json_document(payload)
        _bounded(size, _REQUEST_MAX_BYTES, "request artifact")
        artifact_id = _request_id(scope, task_id)
        await self._store_json(
            artifact_id=artifact_id,
            scope=scope,
            task_id=task_id,
            kind="REQUEST",
            media_type="application/json",
            content=payload,
            fingerprint=fingerprint,
            byte_size=size,
            occurred_at=occurred_at,
        )
        return TaskPayloadReferences(
            input_reference=_reference(artifact_id),
            input_fingerprint=fingerprint,
        )

    async def load(
        self,
        *,
        request_reference: str,
        request_fingerprint: str,
    ) -> ProviderInvocationPayload:
        artifact_id = _artifact_id(request_reference)
        statement = sa.select(client_artifacts).where(
            client_artifacts.c.artifact_id == artifact_id,
            client_artifacts.c.kind == "REQUEST",
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            raise ArtifactNotFound("request artifact not found")
        if cast(str, row["sha256"]) != request_fingerprint:
            raise ArtifactConflict("request fingerprint does not match durable content")
        payload = row["content_json"]
        if not isinstance(payload, dict):
            raise ArtifactConflict("request artifact content shape is invalid")
        materialized = cast(dict[str, object], payload)
        raw_input = materialized.get("input_text")
        if not isinstance(raw_input, str) or not raw_input.strip():
            raw_input = materialized.get("text")
        input_text = (
            raw_input
            if isinstance(raw_input, str) and raw_input.strip()
            else _canonical_json(materialized)
        )
        instructions = materialized.get("instructions")
        if instructions is not None and not isinstance(instructions, str):
            raise ValueError("instructions must be a string when present")
        return ProviderInvocationPayload(
            input_text=input_text,
            instructions=cast(str | None, instructions),
            structured_output=_structured_output(materialized),
        )

    async def store_result(
        self,
        *,
        scope: OwnershipScope,
        task_id: UUID,
        result: dict[str, object],
        occurred_at: datetime,
    ) -> str:
        _aware(occurred_at, "occurred_at")
        _, fingerprint, size = _json_document(result)
        _bounded(size, _RESULT_MAX_BYTES, "result artifact")
        artifact_id = _result_id(scope, task_id)
        await self._store_json(
            artifact_id=artifact_id,
            scope=scope,
            task_id=task_id,
            kind="RESULT",
            media_type="application/json",
            content=result,
            fingerprint=fingerprint,
            byte_size=size,
            occurred_at=occurred_at,
        )
        return _reference(artifact_id)

    async def read_result(
        self,
        *,
        scope: OwnershipScope,
        result_reference: str,
    ) -> dict[str, object] | None:
        try:
            artifact_id = _artifact_id(result_reference)
        except ArtifactNotFound:
            return None
        statement = sa.select(client_artifacts).where(
            client_artifacts.c.artifact_id == artifact_id,
            client_artifacts.c.tenant_id == scope.tenant_id,
            client_artifacts.c.client_id == scope.client_id,
            client_artifacts.c.kind.in_(("RESULT", "PROVIDER_RESPONSE")),
        )
        async with self._sessions() as database:
            row = (await database.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        if row["kind"] == "RESULT":
            value = row["content_json"]
            if not isinstance(value, dict):
                raise ArtifactConflict("result artifact content shape is invalid")
            return dict(cast(dict[str, object], value))

        content = cast(str, row["content_text"])
        output_kind = cast(str, row["output_kind"])
        if output_kind == OutputKind.STRUCTURED.value:
            try:
                decoded = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ArtifactConflict("structured provider response is invalid JSON") from exc
            if isinstance(decoded, dict):
                return cast(dict[str, object], decoded)
            return {"value": decoded}
        return {"output_text": content}

    async def store(
        self,
        *,
        attempt_id: UUID,
        target: ProviderTarget,
        output_kind: OutputKind,
        content: str,
    ) -> str:
        fingerprint, size = _text_digest(content)
        _bounded(size, _PROVIDER_RESPONSE_MAX_BYTES, "provider response artifact")
        if not content:
            raise ValueError("provider response content cannot be empty")

        attempt_statement = sa.select(
            provider_attempts.c.tenant_id,
            provider_attempts.c.client_id,
            provider_attempts.c.task_id,
            provider_attempts.c.provider_id,
            provider_attempts.c.model_id,
            provider_attempts.c.reasoning_profile,
        ).where(provider_attempts.c.attempt_id == attempt_id)

        async with self._sessions() as database:
            attempt = (await database.execute(attempt_statement)).mappings().one_or_none()
        if attempt is None:
            raise ArtifactNotFound("provider attempt not found")
        if (
            cast(str, attempt["provider_id"]) != target.provider_id
            or cast(str, attempt["model_id"]) != target.model_id
            or cast(str, attempt["reasoning_profile"]) != target.reasoning_profile
        ):
            raise ArtifactConflict("provider response target does not match durable attempt")

        scope = OwnershipScope(
            tenant_id=cast(UUID, attempt["tenant_id"]),
            client_id=cast(UUID, attempt["client_id"]),
        )
        artifact_id = _provider_response_id(attempt_id)
        media_type = (
            "application/json"
            if output_kind is OutputKind.STRUCTURED
            else "text/plain; charset=utf-8"
        )
        await self._store_text(
            artifact_id=artifact_id,
            scope=scope,
            task_id=cast(UUID | None, attempt["task_id"]),
            attempt_id=attempt_id,
            media_type=media_type,
            output_kind=output_kind.value,
            content=content,
            fingerprint=fingerprint,
            byte_size=size,
        )
        return _reference(artifact_id)

    async def store_exchange(
        self,
        *,
        scope: OwnershipScope,
        evidence: ClientExchangeEvidence,
        occurred_at: datetime,
    ) -> None:
        _aware(occurred_at, "occurred_at")
        request_size = len(evidence.request_evidence.sanitized_raw_body.encode("utf-8"))
        _bounded(request_size, _EVIDENCE_MAX_BYTES, "request exchange evidence")
        response = evidence.response_evidence
        response_size = (
            None
            if response is None
            else len(response.sanitized_raw_body.encode("utf-8"))
        )
        if response_size is not None:
            _bounded(response_size, _EVIDENCE_MAX_BYTES, "response exchange evidence")

        attempt_statement = sa.select(
            provider_attempts.c.tenant_id,
            provider_attempts.c.client_id,
            provider_attempts.c.task_id,
            provider_attempts.c.provider_id,
            provider_attempts.c.operation,
        ).where(provider_attempts.c.attempt_id == evidence.attempt_id)

        async with self._sessions.begin() as database:
            attempt = (await database.execute(attempt_statement)).mappings().one_or_none()
            if attempt is None:
                raise ArtifactNotFound("provider attempt not found")
            if (
                cast(UUID, attempt["tenant_id"]) != scope.tenant_id
                or cast(UUID, attempt["client_id"]) != scope.client_id
            ):
                raise ArtifactNotFound("owned provider attempt not found")
            if (
                cast(str, attempt["provider_id"]) != evidence.provider_id
                or cast(str, attempt["operation"]) != evidence.operation
            ):
                raise ArtifactConflict("exchange metadata does not match durable attempt")

            values = {
                "exchange_id": evidence.exchange_id,
                "attempt_id": evidence.attempt_id,
                "task_id": attempt["task_id"],
                "tenant_id": scope.tenant_id,
                "client_id": scope.client_id,
                "provider_id": evidence.provider_id,
                "provider_name": evidence.provider_name,
                "operation": evidence.operation,
                "status": evidence.status,
                "status_label": evidence.status_label,
                "request_media_type": evidence.request_evidence.media_type,
                "request_body": evidence.request_evidence.sanitized_raw_body,
                "request_sha256": evidence.request_evidence.sanitized_sha256,
                "request_truncated": evidence.request_evidence.truncated,
                "request_byte_size": request_size,
                "response_media_type": None if response is None else response.media_type,
                "response_body": None if response is None else response.sanitized_raw_body,
                "response_sha256": None if response is None else response.sanitized_sha256,
                "response_truncated": None if response is None else response.truncated,
                "response_byte_size": response_size,
                "created_at": occurred_at,
            }
            inserted = (
                await database.execute(
                    insert(client_exchange_evidence)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[client_exchange_evidence.c.exchange_id]
                    )
                    .returning(client_exchange_evidence.c.exchange_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                current = (
                    await database.execute(
                        sa.select(client_exchange_evidence).where(
                            client_exchange_evidence.c.exchange_id == evidence.exchange_id
                        )
                    )
                ).mappings().one()
                immutable = {
                    key: current[key]
                    for key in values
                    if key != "created_at"
                }
                expected = {key: value for key, value in values.items() if key != "created_at"}
                if immutable != expected:
                    raise ArtifactConflict("exchange evidence identity conflict")

    async def list_for_attempt(
        self,
        *,
        scope: OwnershipScope,
        attempt_id: UUID,
    ) -> tuple[ClientExchangeEvidence, ...]:
        statement = (
            sa.select(client_exchange_evidence)
            .where(
                client_exchange_evidence.c.tenant_id == scope.tenant_id,
                client_exchange_evidence.c.client_id == scope.client_id,
                client_exchange_evidence.c.attempt_id == attempt_id,
            )
            .order_by(
                client_exchange_evidence.c.created_at,
                client_exchange_evidence.c.exchange_id,
            )
        )
        async with self._sessions() as database:
            rows = (await database.execute(statement)).mappings().all()

        items: list[ClientExchangeEvidence] = []
        for row in rows:
            response = None
            if row["response_body"] is not None:
                response = SanitizedEvidenceRecord(
                    media_type=cast(str, row["response_media_type"]),
                    sanitized_raw_body=cast(str, row["response_body"]),
                    sanitized_sha256=cast(str, row["response_sha256"]),
                    truncated=cast(bool, row["response_truncated"]),
                )
            items.append(
                ClientExchangeEvidence(
                    exchange_id=cast(UUID, row["exchange_id"]),
                    attempt_id=cast(UUID, row["attempt_id"]),
                    provider_id=cast(str, row["provider_id"]),
                    provider_name=cast(str, row["provider_name"]),
                    operation=cast(str, row["operation"]),
                    status=cast(str, row["status"]),
                    status_label=cast(str, row["status_label"]),
                    request_evidence=SanitizedEvidenceRecord(
                        media_type=cast(str, row["request_media_type"]),
                        sanitized_raw_body=cast(str, row["request_body"]),
                        sanitized_sha256=cast(str, row["request_sha256"]),
                        truncated=cast(bool, row["request_truncated"]),
                    ),
                    response_evidence=response,
                )
            )
        return tuple(items)

    async def purge_owned(
        self,
        *,
        scope: OwnershipScope,
        cutoffs: ArtifactRetentionCutoffs,
    ) -> int:
        evidence_delete = sa.delete(client_exchange_evidence).where(
            client_exchange_evidence.c.tenant_id == scope.tenant_id,
            client_exchange_evidence.c.client_id == scope.client_id,
            client_exchange_evidence.c.created_at < cutoffs.evidence_before,
        )
        artifact_delete = sa.delete(client_artifacts).where(
            client_artifacts.c.tenant_id == scope.tenant_id,
            client_artifacts.c.client_id == scope.client_id,
            sa.or_(
                sa.and_(
                    client_artifacts.c.kind == "REQUEST",
                    client_artifacts.c.created_at < cutoffs.request_before,
                ),
                sa.and_(
                    client_artifacts.c.kind == "RESULT",
                    client_artifacts.c.created_at < cutoffs.result_before,
                ),
                sa.and_(
                    client_artifacts.c.kind == "PROVIDER_RESPONSE",
                    client_artifacts.c.created_at
                    < cutoffs.provider_response_before,
                ),
            ),
        )
        async with self._sessions.begin() as database:
            evidence_result = await database.execute(evidence_delete)
            artifact_result = await database.execute(artifact_delete)
        return max(0, evidence_result.rowcount or 0) + max(
            0,
            artifact_result.rowcount or 0,
        )

    async def purge_owned_all(
        self,
        *,
        scope: OwnershipScope,
    ) -> int:
        evidence_delete = sa.delete(client_exchange_evidence).where(
            client_exchange_evidence.c.tenant_id == scope.tenant_id,
            client_exchange_evidence.c.client_id == scope.client_id,
        )
        artifact_delete = sa.delete(client_artifacts).where(
            client_artifacts.c.tenant_id == scope.tenant_id,
            client_artifacts.c.client_id == scope.client_id,
        )
        async with self._sessions.begin() as database:
            evidence_result = await database.execute(evidence_delete)
            artifact_result = await database.execute(artifact_delete)
        return max(0, evidence_result.rowcount or 0) + max(
            0,
            artifact_result.rowcount or 0,
        )

    async def delete_owned_before(
        self,
        *,
        scope: OwnershipScope,
        older_than: datetime,
    ) -> int:
        _aware(older_than, "older_than")
        return await self.purge_owned(
            scope=scope,
            cutoffs=ArtifactRetentionCutoffs(
                request_before=older_than,
                result_before=older_than,
                provider_response_before=older_than,
                evidence_before=older_than,
            ),
        )

    async def _store_json(
        self,
        *,
        artifact_id: UUID,
        scope: OwnershipScope,
        task_id: UUID,
        kind: str,
        media_type: str,
        content: dict[str, object],
        fingerprint: str,
        byte_size: int,
        occurred_at: datetime,
    ) -> None:
        values = {
            "artifact_id": artifact_id,
            "tenant_id": scope.tenant_id,
            "client_id": scope.client_id,
            "task_id": task_id,
            "attempt_id": None,
            "kind": kind,
            "media_type": media_type,
            "content_json": content,
            "content_text": None,
            "output_kind": None,
            "sha256": fingerprint,
            "byte_size": byte_size,
            "created_at": occurred_at,
        }
        async with self._sessions.begin() as database:
            inserted = (
                await database.execute(
                    insert(client_artifacts)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=[client_artifacts.c.artifact_id])
                    .returning(client_artifacts.c.artifact_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                current = (
                    await database.execute(
                        sa.select(client_artifacts).where(
                            client_artifacts.c.artifact_id == artifact_id
                        )
                    )
                ).mappings().one()
                if (
                    current["tenant_id"] != scope.tenant_id
                    or current["client_id"] != scope.client_id
                    or current["task_id"] != task_id
                    or current["kind"] != kind
                    or current["sha256"] != fingerprint
                ):
                    raise ArtifactConflict("artifact identity conflict")

    async def _store_text(
        self,
        *,
        artifact_id: UUID,
        scope: OwnershipScope,
        task_id: UUID | None,
        attempt_id: UUID,
        media_type: str,
        output_kind: str,
        content: str,
        fingerprint: str,
        byte_size: int,
    ) -> None:
        values = {
            "artifact_id": artifact_id,
            "tenant_id": scope.tenant_id,
            "client_id": scope.client_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "kind": "PROVIDER_RESPONSE",
            "media_type": media_type,
            "content_json": sa.null(),
            "content_text": content,
            "output_kind": output_kind,
            "sha256": fingerprint,
            "byte_size": byte_size,
            "created_at": sa.func.now(),
        }
        async with self._sessions.begin() as database:
            inserted = (
                await database.execute(
                    insert(client_artifacts)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=[client_artifacts.c.artifact_id])
                    .returning(client_artifacts.c.artifact_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                current = (
                    await database.execute(
                        sa.select(client_artifacts).where(
                            client_artifacts.c.artifact_id == artifact_id
                        )
                    )
                ).mappings().one()
                if (
                    current["tenant_id"] != scope.tenant_id
                    or current["client_id"] != scope.client_id
                    or current["attempt_id"] != attempt_id
                    or current["sha256"] != fingerprint
                    or current["output_kind"] != output_kind
                ):
                    raise ArtifactConflict("provider response artifact identity conflict")
