# ADR — Perplexity Search Intelligence boundary

**Status:** Accepted  
**Date:** 2026-10-05  
**Issue:** #29

## Context

The RASAi characterization intentionally places the current Perplexity Search API
outside its canonical evidence-bound AI provider registry and outside its main AUTO
provider pool. That separation is semantic, not a temporary rollout state.

ORQETIA is a provider-neutral AI orchestration service. Importing a domain-specific
search integration into the canonical LLM provider registry merely because the same
vendor also exposes AI products would collapse two different contracts:

- model inference / generation;
- external web search / source discovery.

They have different request/response shapes, provenance requirements, privacy risks,
usage units, retry semantics, and product expectations.

## Decision

The **Perplexity Search API is not an ORQETIA canonical AI provider** and must not be
registered in the Provider Registry or participate in AUTO / EXPLICIT_TARGET model
orchestration.

No Perplexity Search adapter is created by the Provider Framework phase.

If ORQETIA later exposes Search Intelligence, it must be a separate capability or
integration boundary with its own operation contract, authorization, provenance,
privacy controls, accounting and lifecycle. It must not masquerade as a model
attempt.

## Reusable abstractions

The boundary does not forbid reuse of genuinely provider-neutral infrastructure.

Reusable examples include:

- native non-token usage dimensions;
- a `PER_REQUEST` accounting unit;
- credential references resolved by the Backoffice/control plane;
- durable external-attempt identifiers;
- common sanitized failure metadata;
- generic rate/quota observation primitives.

Reuse of those primitives does **not** import RASAi Search Intelligence domain
objects or make Perplexity Search part of the AI provider registry.

## Future Perplexity LLM surfaces

Any future Perplexity model/inference surface must be evaluated independently from
the Search API.

A future issue must characterize, at minimum:

- exact API/product surface and endpoint;
- model and reasoning semantics;
- structured-output guarantees;
- usage/error normalization;
- tool/search behavior and whether it can be disabled;
- data-retention/privacy implications;
- cost/accounting comparability;
- single-attempt ambiguity behavior.

If such a surface is later admitted to the canonical Provider Registry, it must obey
the same ORQETIA rules as other providers. Vendor identity alone is not sufficient
for admission, and the Search API decision neither pre-approves nor rejects a future
LLM adapter.

## Privacy and security

Search queries can disclose client context to an external research service. A future
Search Intelligence capability therefore requires an explicit authorization and data
classification boundary. It must not silently receive materialized LLM prompts,
provider credentials, private accounting data, or broader client evidence simply
because the Perplexity vendor is configured.

Provider/vendor secrets remain Backoffice-only.

## Accounting

Perplexity Search may naturally use request-based native accounting rather than token
usage. ORQETIA may reuse a generic native usage contract under the canonical
accounting work, but must not fabricate input/output tokens or monetary cost when the
provider surface does not supply a reproducible mapping.

## Consequences

- no Perplexity Search entry in the canonical AI Provider Registry;
- no participation in AUTO or explicit LLM target selection;
- no Search API implementation in the current Provider Framework;
- no coupling from AI adapters to Search Intelligence;
- a future Search Intelligence feature requires a dedicated issue/contract;
- a future Perplexity LLM product requires a new provider-characterization issue.

## Read-only characterization sources

The decision was characterized from RASAi's current read-only Search Intelligence
boundary, including its Perplexity integration, provider-registry documentation and
native-usage accounting behavior.

RASAi remains read-only and is not a runtime/build dependency.
