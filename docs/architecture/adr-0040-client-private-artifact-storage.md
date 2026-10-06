# ADR — Durable client-private artifact storage

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #144

## Decision

ORQETIA stores client request payloads, client-safe results and sanitized exchange evidence behind typed ports. The M0 production adapter is PostgreSQL and remains replaceable by another physical strategy without changing Execution domain records.

Execution tasks continue to persist only opaque references and SHA-256 fingerprints. Work items continue to carry identifiers only; raw payload content is never copied into the queue/outbox.

## Ownership and references

Public/client reads always require the authenticated tenant/client ownership scope. Artifact references are opaque ORQETIA-owned identifiers and contain no payload, provider credential or commercial metadata.

The provider request port predates #144 and intentionally accepts only reference + fingerprint. It is an internal already-authorized boundary: the orchestration layer must first load the owned task/attempt, then hand its request reference to the provider adapter. The PostgreSQL adapter revalidates the immutable fingerprint before materializing content.

Provider response writes do not accept caller-supplied ownership. The adapter derives tenant/client from the durable ProviderAttempt and verifies the exact provider/model/reasoning target before storing output.

## Storage bounds

- client request JSON: 1 MiB;
- client result JSON: 2 MiB;
- provider response content: 2 MiB;
- each sanitized request/response evidence body: 256 KiB.

Oversize content fails closed and is never moved to messaging.

## Result boundary

Provider adapters persist only their normalized output content, never the raw provider envelope. Client result reads therefore cannot project provider account, credential, secret, pricing, currency, credits or provider_cost from storage metadata.

Structured provider output is returned as JSON; text output is projected as output_text.

## Evidence

Only SanitizedEvidenceRecord values are accepted for client exchange evidence. Their SHA-256 is verified against the sanitized UTF-8 body. Evidence rows are ownership-scoped and bound to an existing ProviderAttempt whose provider and operation match.

This storage layer does not reinterpret or humanize sanitized raw evidence.

## Retention/deletion

The adapter exposes an ownership-scoped delete-before primitive. Policy schedules and legal retention periods remain governed by #55/hardening; #144 supplies the physical deletion capability without inventing retention periods.

## Composition

The API composition root constructs one shared PostgresClientArtifactStore from the process session factory. #145 reuses the same adapter for provider request/response materialization in the orchestration worker.

## Validation

The isolated PostgreSQL test covers request fingerprint/idempotency, provider response ownership, result projection, sanitized evidence ownership, explicit size limits and retention deletion. No provider or IdP call is made.
