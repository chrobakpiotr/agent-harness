# AH5-06 — Release and controlled cutover

- Status: **accepted**; integration authorised. 06a (rehearsal) in progress; 06b (cutover) waits for 04b/04c.
- Source: master plan AH5-06; depends on AH5-05, independent review, integration authorisation. Showcase input:
  `91d4671` (`tooling/agent-harness/` unchanged since `50c18f9`), read-only in 06a.

## Findings (2026-10-06)

- Showcase modules that stay (`executor`, `supervisor`, `workspace`, `verify.py`, `verification_command`,
  `harness`, `orchestrate`, `runner`, `design`, `wayfinder`, `verification_contract`) import the moved ones,
  partly through private names (`store._validate_component`, `store` internals patched by Showcase tests).
- The plan AC "qualified track actually qualified" needs 04b/04c; Showcase `main` carries other active work.
- The cutover pins a released library version; the tag push is authorised once the cutover checks pass.

## AH5-06a — Rehearsal (no change to Showcase)

- Allowed paths: `docs/specs/AH5-06/`, `docs/migration/cutover.md`, `migration/showcase-wrapper/`,
  `scripts/rehearse-cutover.sh`.
- Method: export Showcase with `git archive` into a temporary directory, replace each moved module with a
  thin wrapper over the installed library wheel (Showcase bindings: profile root, issuer registry, lifecycle
  = `harness`), and run the same checks on the unmodified export (baseline) and the wrapped one.
- AC1: the Showcase harness test suite gives the same pass/fail set on both; every difference is listed with
  its cause.
- AC2: the Showcase CI protocol commands (`harness.py doctor`, `validate-all`, `verification_contract.py
  validate`, `spec_inventory.py --check`) give the same exit code and output on both.
- AC3: rollback rehearsal: state written by the wrapped export is read by the restored original scripts on
  the same disposable repository (single-writer rule, ADR 0001).
- AC4: `docs/migration/cutover.md` operator guide: install, pin, boundaries, rollback.
- AC5: the Showcase repository is unchanged (`git status` and `HEAD` identical before and after).

## AH5-06b — Cutover (later)

Version bump, annotated tag pushed with `main`, Showcase pins the tag SHA and replaces the moved modules
with the wrapper; plan AC (lifecycle/CLI parity, qualified track qualified, no second implementation owner).
Not drafted until 04b/04c land.
