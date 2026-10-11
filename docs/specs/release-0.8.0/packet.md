# Version 0.8.0 — retries, Linux targets, qualified grading layout

- Status: accepted (release ordered 2026-10-11). Distribution: annotated Git tag `v0.8.0`; consumers pin the tag SHA.
- Scope: AH5-04c follow-ups (`docs/specs/AH5-04c/packet.md`, "Retries, Linux targets, grading-layout qualification").
  Two independent evaluation rounds (conditions fixed); independent review of the new reference (pass).
- Consumers: agent-benchmark grading sessions; Showcase 04c. A session needs this version's shipped reference.

## Acceptance criteria

- AC1: version 0.8.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.0` pushed with `main`; a fresh install from the tag SHA ships a passing reviewed reference
  whose probe and policy digests equal the installed ones and whose tuple states `grading_layout: qualified`.

## Evidence (2026-10-11)

- AC1: `__version__` 0.8.0; CHANGELOG `## 0.8.0 — 2026-10-11`; README pins `@v0.8.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 219 tests OK, 2 skipped. CI run 38127580873 for `bcc0f13`:
  success.
- AC3: `refs/tags/v0.8.0` = `5c0eb4db` → commit `bcc0f130205cd298d76b9ad51ba2f5731bef3241`, pushed with `main`. Fresh
  CPython 3.13 venv from the tag commit, empty directory: `agent-harness 0.8.0`; `reviewed_reference()` passes, its
  probe and policy digests equal the installed ones, and `grading_layout` is `qualified`.
