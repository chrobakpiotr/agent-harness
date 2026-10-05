# AH5-01 — Extraction map and ownership

- Status: accepted.
- Source: `agent-harness-agent-plan-2026-10-05.md`, section AH5-01. Depends on AH5-00.
- Showcase is read-only input (`50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`).

## Allowed paths

`docs/migration/source-map.md`, `docs/adr/0001-extraction-ownership.md`, `docs/specs/AH5-01/`,
`tests/test_boundaries.py`.

## Out of scope

Copying source, contract fixtures (AH5-02), AMQP quarantine, any change in Showcase.

## Acceptance criteria

- AC1: no consumer import cycle — the package imports only stdlib and itself (mechanical test).
- AC2: diagnostic/incomplete components are marked in the map with their source locations.
- AC3: rollback plan and single-writer state rule are written down.
- AC4: per module: source SHA, imports, resources, policy/state coupling, tests, owner after cutover;
  public CLI, formats, Git common dir, profile discovery, clock/process dependencies mapped.

## Evidence (2026-10-05)

- AC1: `tests/test_boundaries.py` on the installed wheel (3.13: run; 3.9: skipped, no
  `sys.stdlib_module_names`). Mutation: adding `import harness` to the package made it FAIL.
- AC2–AC4: `docs/migration/source-map.md`; ADR 0001 (Proposed). Survey commands: `wc -l`, import
  and `__file__`/cwd/git/env greps over Showcase `tooling/agent-harness/`; guard lines read in
  `verification/authority.py:135`, `verification/executor.py:127`, `verification_sandbox.py:doctor`,
  `verification/workspace.py:118`.
- Owner columns and ADR are proposals; final ownership needs independent review.
