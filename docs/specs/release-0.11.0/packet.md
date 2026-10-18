# Version 0.11.0 — the verification supervisor, command backend and executor (AH5-08b)

- Status: accepted (release ordered by the owner 2026-10-18). Distribution: annotated Git tag `v0.11.0`; consumers pin the
  tag SHA.
- Scope: AH5-08b (`docs/specs/AH5-08/packet.md`). The supervisor, command backend and executor move from Showcase
  `49ab97f` behind explicit lifecycle, command-policy and retry-control ports. Showcase's retry registry leaves the
  library store. The contract, `probe_digest` and the shipped reviewed reference are unchanged.

## Acceptance criteria

- AC1: version 0.11.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.11.0` pushed with `main`. A fresh install from the tag SHA reports 0.11.0, and its shipped
  reference matches the installed probe digest.

## Evidence (2026-10-18)

- AC1: `__version__` 0.11.0; CHANGELOG `## 0.11.0 — 2026-10-18`; README pins `@v0.11.0`.
- AC2: `scripts/check-wheel.sh` on the release commit `b6c269b`: 412 tests OK, 2 skipped. CI for `2188c8b`
  (the release commit plus the recorded evaluation verdict): success.
- AC3: `refs/tags/v0.11.0` = `54f8f640` → commit `2188c8b3ef03d338611cdff95065f9ce04009dbf`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag reports `agent-harness 0.11.0`.
  - The supervisor, command, executor and retry modules import.
  - `reviewed_reference()` passes, and the shipped reference carries the installed probe digest
    (`sha256:ee87222a…`).

