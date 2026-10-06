# ADR — Administrative provider/model/capability catalog

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #140

## Decision

Control Plane owns immutable administrative versions of the provider inventory.
The runtime provider contract remains the canonical `ProviderRegistry` from #9.

Each catalog version stores the exact `ProviderSpec` graph:

- provider approval and AUTO/EXPLICIT_TARGET eligibility;
- model approval/eligibility;
- offered versus approved capabilities;
- reasoning profiles/defaults;
- adapter key/protocol metadata.

Publishing creates a new immutable version and atomically moves a singleton active
assignment using optimistic assignment versions.

## Runtime bridge

The active catalog materializes a fresh `ProviderRegistry`. Therefore all fail-closed
registry behavior from #9 remains authoritative: unapproved providers/models/profiles
stay unavailable, defaults must point to approved items, and capabilities remain
offered-versus-approved.

The administrative layer does not duplicate target resolution or AUTO/EXPLICIT_TARGET
semantics.

## Endpoint metadata

A catalog may also carry non-secret provider endpoint metadata for adapters that need a
configured upstream endpoint. Endpoint URLs are administrative-only and must be HTTPS,
contain no userinfo, query or fragment, and refer to a provider present in the same
catalog.

Endpoint metadata never contains provider credentials. Client requests cannot override
these values.

## Security

No provider secret, account credential, commercial cost or client authorization is
stored in this catalog.

Catalog approval does not grant a tenant/client target authorization. The execution
policy envelope from #137 remains an independent required authorization boundary.

## Validation

Validation is isolated to migration plus version/activation/registry-materialization
tests. No provider call is made and automation cost remains zero.
