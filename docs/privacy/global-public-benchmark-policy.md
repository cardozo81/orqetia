# GLOBAL_PUBLIC benchmark privacy contract

**Status:** Engineering policy v1  
**Contract version:** `global-public-v1`  
**Issue:** #158  
**Related:** #11, #55, #61, #149, #157  
**Product lifecycle:** DEVELOPMENT

This document is the technical privacy contract for ORQETIA
`GLOBAL_PUBLIC` statistical token-estimation benchmarks. It is not a legal
notice, a commercial term or a declaration that the product is public/GA.

## Purpose limitation

The only baseline purpose is:

`technical_token_estimation`

GLOBAL_PUBLIC aggregates may support provider-free technical token/usage
estimation. They are not a raw cross-customer dataset and are not authorized by
this contract for training/fine-tuning, advertising, profiling, resale or
customer-content search.

A new purpose requires an explicit privacy/product review under #55 and, when
applicable, a new privacy-contract version.

## Allowed source data

The benchmark source contract contains only:

- tenant/client identity needed **internally** to enforce cohort diversity;
- typed non-content feature dimensions;
- technical token counts;
- occurrence time;
- an explicit `global_eligible` flag.

The only GLOBAL_PUBLIC feature dimensions are:

- provider ID;
- model ID;
- reasoning profile;
- input-size bucket;
- output class;
- schema class.

These are fixed typed dimensions. Callers cannot add ad-hoc filters to narrow a
public cohort.

## Prohibited data

GLOBAL_PUBLIC benchmark inputs/outputs must not contain or derive a public row
from:

- raw prompt, input, context, output or other client content;
- user/data-subject identifiers, email/IP or direct personal identifiers;
- raw tenant/client identifiers in the published snapshot;
- provider credentials/secrets/accounts;
- provider cost, currency, client_charge or other financial terms.

Pseudonymizing a client identifier does not make it acceptable public benchmark
data.

## Anti-reidentification floors

`global-public-v1` has non-lowerable engineering privacy floors:

| Control | Minimum |
| --- | ---: |
| eligible technical samples | 30 |
| distinct tenant/client pairs | 5 |
| public sample-count bucket | 10 |
| public cohort-count bucket | 5 |

A methodology may choose **higher** thresholds/buckets. It cannot configure a
weaker value while still claiming this privacy-contract version.

A single high-volume customer can never satisfy the distinct-client floor.

Exact internal counts may exist for governance/rebuild, but client-facing
metadata exposes bucketed counts and GLOBAL_PUBLIC snapshots carry no
tenant/client owner.

## Eligibility, opt-out and deletion

`BenchmarkSample.global_eligible=False` means the sample is excluded from
GLOBAL_PUBLIC construction.

When an applicable privacy/contractual instruction removes eligibility:

1. stop admitting the affected source fact to future GLOBAL_PUBLIC builds;
2. mark/rebuild affected aggregate snapshots through the governed benchmark
   rebuild process;
3. recompute cohort/sample thresholds from the remaining eligible data;
4. fail closed if the rebuilt cohort no longer meets `global-public-v1`;
5. do not silently retain the old aggregate as a replacement for a rebuild;
6. apply #149 retention/deletion rules to underlying eligible data where
   applicable.

This is not a claim that every legal deletion request always requires physical
erasure of every historical security/legal record; #55 governs those
exceptions. It is the statistical-product behavior.

## Quality and rebuild interaction

#157 quality rules remain mandatory. Privacy eligibility and quality are
independent gates:

- a privacy-safe cohort can still be stale/drifted/miscalibrated;
- a high-quality snapshot cannot bypass the cohort/privacy floor.

Rebuild creates a new immutable benchmark version with `as_of` and methodology
provenance.

## Recipients and subprocessors

The GLOBAL_PUBLIC benchmark contract itself has two technical recipient classes:

- authorized ORQETIA clients receiving only approved aggregate estimate metadata;
- ORQETIA internal estimation/read services processing the governed aggregate.

This contract does not require an external analytics or AI subprocessor.

Hosting, telemetry, database, identity or other deployment-specific processors
must be recorded in the #55 processing inventory/notice when actually selected.
This document does not invent vendors or legal roles that are not configured.

## Transparency

Before any public RC/GA statement that GLOBAL_PUBLIC is available, product
documentation/privacy notice must accurately describe:

- the technical estimation purpose;
- aggregate categories and cohort safeguards;
- sharing/recipient categories and configured subprocessors;
- retention/rebuild behavior;
- opt-out/deletion handling where applicable;
- contact/rights mechanisms required by #55.

Final legal wording/review may be a human gate. Engineering enforcement does not
wait for that wording and must remain fail-closed.

## Validation

The isolated `GLOBAL_PUBLIC privacy` gate proves:

- privacy floors cannot be weakened by methodology configuration;
- stronger thresholds remain valid;
- feature dimensions are allowlisted;
- raw content/identifier/financial fields are absent from the benchmark sample;
- GLOBAL_PUBLIC output strips owner identity and exposes bucketed counts;
- opt-out followed by rebuild can make a formerly valid cohort fail closed.

No provider call or paid service is used.
