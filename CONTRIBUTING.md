# Contributing

ORQETIA is in **DEVELOPMENT**.

## Hard repository boundary

All writes belong to `cardozo81/orqetia`.

Do not mutate `cardozo81/RASAI-Readiness-Auditor`. It is read-only reference material.

## Source of work

Use #47 as the operational roadmap.

An executable issue should include:
- Parent;
- objective;
- scope and out of scope;
- Blocked by;
- Blocks;
- risk;
- acceptance criteria;
- targeted tests;
- canonical references when applicable;
- zero-cost automation constraints;
- human gate only when genuinely required.

## Implementation principles

1. Reuse/extract proven behavior before rewriting it.
2. Preserve canonical semantics unless an ADR explicitly changes them.
3. Keep changes limited to the issue boundary.
4. Test the changed boundary first; widen regression only when dependency analysis justifies it.
5. Do not call paid AI providers in ordinary CI.
6. Use GitHub Agent/Actions automatically only when doing so creates no additional financial cost for the repository owner.
7. Keep OpenAPI and technical documentation synchronized with public contract changes.
8. Human smoke is primarily a functional-package/RC activity, not a default PR gate.

## Product state

Do not describe the project as prerelease, RC, stable, production-ready or GA while #46 remains in DEVELOPMENT state.


## Closed issue contract changes

Follow #84.

Closed/completed issues must not receive silent material contract changes.

- Non-material fixes: typo, formatting, broken links and references without semantic change.
- Material changes: requirements, API/schema, authorization, security/privacy, persistence, architecture, dependencies, tests or acceptance criteria.

For a material change, reopen the original issue before editing or create a linked delta/addendum issue that explicitly amends it. Any reopened dependency blocks downstream implementation again until revalidated.
