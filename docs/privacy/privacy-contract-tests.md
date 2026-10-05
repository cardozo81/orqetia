# Privacy and lifecycle contract tests — #55

Future implementation must cover synthetic data only.

## Isolation/minimization

- [ ] client A raw input/output never appears in client B queries/exports;
- [ ] GLOBAL_PUBLIC pipeline rejects raw prompt/output and tenant identifiers;
- [ ] sparse/single-tenant aggregate fails cohort-publication policy;
- [ ] logs/traces do not contain raw prompt/output by default;
- [ ] provider route receives only the task data required by its adapter contract.

## Retention/deletion

- [ ] every persisted privacy category resolves a retention policy;
- [ ] missing retention configuration fails policy validation rather than becoming infinite;
- [ ] expired eligible source rows/blobs are deleted/anonymized idempotently;
- [ ] projections/read models are removed/rebuilt after deletion;
- [ ] repeated deletion request is idempotent;
- [ ] crash mid-deletion resumes from durable state;
- [ ] deletion tombstone prevents backup/replay from resurrecting active data;
- [ ] legal/security hold prevents only the covered data from deletion and records reason.

## Rights requests

- [ ] subject identity/authority is verified before export/correction/deletion;
- [ ] export cannot include another tenant/client;
- [ ] correction propagates to affected projections;
- [ ] controller vs operator routing follows processing inventory;
- [ ] privacy request deadline is tracked;
- [ ] audit record contains safe evidence, not the exported dataset.

## Provider/subprocessor privacy

- [ ] technically enabled provider without required privacy approval is not selected for restricted personal-data route;
- [ ] provider record exposes transfer/region/privacy metadata to administrative policy;
- [ ] client cannot override provider privacy approval/region through task input;
- [ ] secret/provider cost fields never enter a data-subject export.

## International transfer

- [ ] route requiring international transfer fails closed when transfer mechanism metadata is absent/expired;
- [ ] valid mechanism/version is snapshotted/referenced for the processing route;
- [ ] changing provider region/mechanism does not rewrite historical processing evidence.

## Incident/privacy integration

- [ ] incident case records controller knowledge timestamp;
- [ ] relevant-risk/damage assessment state is durable;
- [ ] operator path produces controller-notification workflow without pretending ORQETIA made controller's legal decision;
- [ ] regulatory incident-record retention policy can enforce the applicable five-year minimum.

## Backup restore

- [ ] restored environment replays deletion/tombstone records before serving ordinary reads;
- [ ] deleted subject data is not silently reintroduced into active read models.
