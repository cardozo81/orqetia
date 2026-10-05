# ADR-0012 — LGPD privacy-by-design and data lifecycle

- Status: **Accepted**
- Issue: #55
- Depends on: #21, #33, #44, #53
- Related: #56, #61, #57
- Product state: DEVELOPMENT

## Legal/regulatory baseline used by this ADR

Engineering requirements were checked against the current official LGPD/ANPD material available at the decision date.

Primary references:
- Lei nº 13.709/2018 — LGPD;
- ANPD guidance on data-subject rights;
- Resolução CD/ANPD nº 15/2024 — security incident communication;
- Resolução CD/ANPD nº 19/2024 — international data transfers;
- ANPD guidance on RIPD.

This ADR is an engineering/privacy architecture contract. It does not choose or certify the legal basis for a customer's processing activity. The competent controller must establish the applicable role, purpose and legal basis.

## Core decision

ORQETIA implements privacy by design through:
- data minimization;
- purpose limitation;
- explicit processing inventory;
- least privilege;
- tenant segregation;
- configurable retention;
- deletion/correction workflows;
- controlled processor/subprocessor sharing;
- international-transfer metadata;
- privacy-impact triggers;
- no cross-client raw-content reuse;
- cohort-safe statistical products only.

Raw client content is not retained, logged or reused merely because it is technically available.

## Controller / operator roles

ORQETIA can have different LGPD roles by processing activity.

### Likely controller activities

For ORQETIA's own operational purposes, examples may include:
- Backoffice employee/admin account administration;
- security/audit operations;
- product billing/account administration when later introduced;
- its own legal/compliance records.

### Likely operator/subprocessor activities

For customer-submitted prompts/context/results, ORQETIA may act on the customer's instructions when the customer determines the purpose/essential means.

Provider companies receiving content may become subprocessors/operators or independent controllers depending on the concrete provider terms/processing.

The processing inventory must record the role per activity. Code must not assume that ORQETIA is always controller or always operator.

## Processing inventory

Every processing purpose records:
- processing_activity_id/version;
- data category/classification;
- data subjects;
- purpose;
- controller/processor role;
- controller-designated legal basis/reference;
- source;
- recipients/subprocessors;
- international-transfer status/mechanism;
- retention rule;
- authoritative owner/store;
- encryption/security requirements;
- data-subject rights applicability/handling;
- automated-decision relevance;
- RIPD/risk status.

The product may enforce that a required privacy configuration exists, but it does not fabricate a legal basis.

## Data minimization

### Client task content

Input/prompt/context:
- may contain arbitrary personal or sensitive data;
- classification defaults to CLIENT_PRIVATE until explicitly reduced;
- persisted only when required by product functionality and according to configured retention;
- excluded from ordinary logs/traces/metrics.

Provider request:
- includes only content necessary for the selected task;
- system/internal metadata not needed by the provider is omitted;
- provider secret/account/cost metadata is not sent as prompt context;
- future redaction/tokenization may be applied when compatible with task semantics.

Output/result:
- treated at least as sensitively as the input that produced it unless classification proves otherwise;
- not copied to GLOBAL_PUBLIC/statistical products as raw content.

### Logs/traces

Default:
- identifiers/correlation IDs;
- status/error class;
- latency/resource metrics;
- safe tenant/client/provider dimensions according to authorization.

Avoid:
- raw prompt/input/output;
- bearer/session/provider credentials;
- full request/response body;
- unnecessary IP/User-Agent persistence.

## No cross-client content reuse

ORQETIA does not use one client's raw prompts, context, outputs or results:
- to fulfill another client's task;
- to create raw shared datasets;
- to populate GLOBAL_PUBLIC benchmarks;
- for model training/fine-tuning under the baseline.

Any future training/improvement use involving client content requires a separate explicit product/legal/privacy decision and opt-in/contract analysis where applicable.

## Statistical products

### CLIENT_ONLY

May use that client's own permitted facts/aggregates.

Raw task content is unnecessary for the baseline statistical estimate architecture.

### GLOBAL_PUBLIC

May contain only aggregates approved as cohort-safe and non-reidentifying.

Rules:
- minimum cohort threshold;
- no raw tenant/client/user identifier;
- no raw prompt/output;
- no single-tenant bucket;
- suppress/split-resistant handling of sparse combinations;
- documented dimensions and release criteria;
- reidentification risk review before publication/use.

Pseudonymizing a tenant/client identifier is not treated as sufficient anonymization by itself.

Exact statistical safeguards are finalized in #11/#61.

## Data-subject rights workflow

The privacy service/runbook supports requests for:
- confirmation of processing;
- access;
- correction;
- anonymization/blocking/deletion where applicable;
- portability where applicable and regulated;
- information about sharing;
- consent revocation where consent is the legal basis;
- objection where applicable;
- review/information regarding solely automated decisions affecting the subject where applicable.

### Identity and authority

Before disclosing/mutating personal data:
- verify requester identity at a level proportional to the data/action;
- verify representation authority where applicable;
- avoid collecting excessive new identity documents solely to answer a request.

### Controller vs operator path

If ORQETIA is controller for the activity:
- process the request under the applicable legal/regulatory deadlines.

If ORQETIA is operator:
- route/assist the controller according to the contract/instructions;
- do not independently make legal decisions reserved to the controller unless required by law.

### Access response

Architecture supports:
- immediate/simplified confirmation where feasible;
- complete data export/report flow;
- source/purpose/criteria/recipient metadata when required;
- machine-readable export where product/legal policy requires.

The LGPD complete access response can have a statutory 15-day deadline; operational implementation should track due dates explicitly.

## Correction propagation

A correction:
1. updates authoritative owner data;
2. emits a privacy correction event where downstream copies exist;
3. rebuilds/invalidate affected read models;
4. propagates to processors/subprocessors when legally/contractually applicable;
5. records completion/evidence without duplicating sensitive data into audit logs.

Historical audit/security records are not silently rewritten when integrity/legal retention requires preservation; they may carry correction/supersession metadata.

## Deletion / anonymization workflow

Deletion is a governed operation, not a single SQL DELETE.

Flow:
1. authenticate/authorize request;
2. identify controller/role/legal applicability;
3. create privacy request case;
4. discover authoritative data by subject/tenant/client mapping;
5. place deletion tombstone/operation ID where needed to prevent resurrection;
6. delete/anonymize authoritative eligible data;
7. delete eligible blob/object/cache data;
8. rebuild/remove projections;
9. instruct subprocessors where applicable;
10. record exceptions and legal/security retention basis;
11. produce safe completion evidence.

Data required for legal obligation, fraud/security, dispute or other lawful retention is isolated and access-restricted rather than falsely reported as deleted.

## Backups

Immutable backup media may not support immediate physical deletion of an individual row.

Baseline:
- deleted data is not restored into active service without reapplying deletion/tombstone records;
- backup retention is bounded;
- restored environments replay privacy deletions before ordinary use;
- backup access is restricted/audited;
- expired backups are securely destroyed according to infrastructure capability.

This limitation is disclosed in internal privacy procedures and reflected in response policy as legally appropriate.

## Retention policy

There is no blanket indefinite retention.

Every category has:
- retention owner;
- start event;
- duration/rule;
- legal/security hold behavior;
- disposal method;
- review cadence.

Raw client content has a minimizing default and must have an explicit retention purpose.

Numeric periods that are legally mandated/established are encoded separately from product defaults.

### Incident records

Under the ANPD incident-security regulation, incident records must be retained for at least five years where the rule applies.

### Product/business data

Exact commercial retention periods for tasks/results/technical usage are product decisions and must be explicitly configured before public RC; absence of a configured retention policy may not mean "forever".

## International transfers

Before enabling a processing path that transfers personal data outside Brazil or to an international recipient, the processing inventory records:
- exporter/importer;
- countries/regions where known;
- data categories/purposes;
- controller/operator roles;
- legal basis for the processing;
- valid international-transfer mechanism;
- provider/subprocessor contract reference;
- security safeguards;
- onward-transfer conditions.

The ANPD international-transfer regulation requires both:
- a valid LGPD processing legal hypothesis; and
- a valid transfer mechanism.

Approved mechanisms may include adequacy decisions, ANPD standard contractual clauses, approved global corporate rules/specific clauses, or other cases legally permitted.

If a required transfer mechanism is absent/invalid, the affected personal-data processing route is not enabled.

Provider region/data-residency configuration is administrative and not client-controlled.

## Provider/subprocessor governance

Provider records must support:
- legal entity/processor role metadata;
- privacy/DPA/terms reference;
- data-use/training setting;
- retention setting where contract/provider supports it;
- regions/data-residency;
- international-transfer mechanism;
- subprocessors reference;
- security/privacy review date.

A provider adapter being technically functional does not automatically make it privacy-approved for all tenants/data classes.

## Automated decision review

If ORQETIA later performs a solely automated decision that affects a natural person's interests:
- the processing inventory marks it;
- criteria/procedure documentation is retained at a safe explanatory level;
- human review/request workflow is supported where applicable;
- protected trade/security details are separated from the understandable explanation.

Ordinary LLM task generation is not automatically classified as a legally significant automated decision; the actual product use case determines this.

## RIPD triggers

Create/review a RIPD before or as early as possible for high-risk treatment, including internal triggers such as:
- large-scale personal/sensitive-data processing;
- systematic processing of sensitive data;
- children/adolescents or other vulnerable subjects;
- new cross-client statistical use;
- new legally significant automated decision/profile;
- new invasive monitoring/behavioral profiling;
- new broad international-transfer arrangement;
- novel combination of datasets that materially raises reidentification risk;
- security/privacy architecture change with high impact.

Also prepare a RIPD when requested by ANPD.

RIPD records:
- processing/data types;
- purpose/necessity/proportionality;
- methodology/security;
- risks to rights/freedoms;
- safeguards/mitigations;
- residual risk/decision;
- owner/review date.

## Security incidents involving personal data

Integration point: #57 incident response.

Privacy requirements:
- preserve time of controller knowledge;
- identify affected categories/subjects/volume;
- assess relevant risk/damage;
- preserve mitigation/action evidence;
- identify controller/operator responsibility.

When ORQETIA is controller and the incident meets the relevant-risk/damage threshold, current ANPD rules require communication to ANPD and affected titulars within three business days, subject to specific-law exceptions.

If complete information is unavailable, a justified preliminary notification may be followed by complementary information within the applicable regulatory period.

When ORQETIA acts as operator, it informs the controller without unjustified delay and provides available facts needed for the controller's notification obligations.

Do not disclose unnecessary SECRET/internal exploit details in public/titular communication.

## Privacy notices and transparency

Before public use, publish/update notices appropriate to ORQETIA's controller activities and support customer/controller documentation for operator activities.

Notice/transparency data should include, as applicable:
- purposes/categories;
- controller contact;
- DPO/encarregado channel where applicable;
- sharing/subprocessors;
- international transfers;
- retention/lifecycle;
- subject-right request channel.

## Privacy request audit

Audit:
- request type;
- subject/requester safe ID;
- controller/activity scope;
- timestamps/deadlines;
- decision/exception category;
- downstream processors contacted;
- completion status.

Do not copy the full exported personal dataset into the audit record.

## Engineering enforcement

Future implementation requires:
- processing-inventory schema;
- privacy request state machine;
- retention/deletion worker;
- tombstone/replay protection;
- projection rebuild hooks;
- provider privacy-approval flag/policy;
- international-transfer metadata;
- privacy tests defined below.

## Consequences

Positive:
- privacy obligations become data/model/workflow requirements;
- provider use is constrained by privacy approval, not only technical availability;
- raw cross-client content is excluded from shared statistics;
- deletion is restart-safe and projection-aware;
- international transfers are explicit.

Costs:
- privacy metadata becomes part of provider/control-plane administration;
- deletion/correction requires cross-context workflows;
- retention policies must be operationally implemented, not just documented.
