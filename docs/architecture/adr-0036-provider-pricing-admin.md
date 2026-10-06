# ADR — Administrative provider pricing catalog

**Status:** Accepted  
**Date:** 2026-10-06  
**Issue:** #141

## Decision

Control Plane owns immutable administrative versions of provider technical pricing.
The pricing semantics themselves remain owned by #16 through canonical
`PricingRule`, `PricingCatalog` and `PricingResolver`.

A catalog publication stores a complete immutable tuple of pricing rules and atomically
moves the singleton active assignment using optimistic assignment versions.

## Canonical bridge

The effective administrative catalog materializes `PricingCatalog` directly. No
parallel price calculator exists in Control Plane.

All canonical models are preserved:

- TOKEN_STANDARD;
- TOKEN_CONTEXT_TIERED;
- TOKEN_TIME_WINDOW;
- PER_REQUEST;
- PROVIDER_CREDITS;
- reasoning billing modes;
- effective date ranges;
- source reference metadata.

## Currency and UNPRICED

Currency remains attached to each canonical rule/quote. The administrative catalog
does not aggregate or convert currencies and introduces no FX.

Missing or unusable pricing continues to resolve as `UNPRICED` with amount/currency
absent. A legitimate priced amount of zero remains priced and is not converted into
UNPRICED.

## Financial boundary

This catalog represents internal provider-cost pricing only. It does not contain
`client_charge`, commercial plan markup or customer billing logic; those remain
explicitly outside M0 in #41.

Pricing data is Backoffice CONFIDENTIAL/RESTRICTED under #61 and is never projected to
Client Portal/client APIs.

## Persistence

Canonical rule graphs are serialized to JSONB inside immutable catalog-version rows.
The serializer round-trip is covered for token standard/tiered/time-window/request and
provider-credit models.

## Validation

Validation is isolated to migration plus version/activation/materialization/currency/
UNPRICED/serialization tests. No paid provider is contacted.
