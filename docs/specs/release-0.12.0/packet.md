# Version 0.12.0 — state snapshots, eval engine, ownership amendment (AH5-09, AH5-10)

- Status: accepted (release ordered by the owner 2026-10-18). Distribution: annotated Git tag `v0.12.0`; consumers pin the
  tag SHA.
- Scope:
  - AH5-09 `agent_harness.state_snapshots`;
  - AH5-10 `agent_harness.eval_runner`;
  - ADR 0001 amendment 2026-10-18 (frozen copies, cutover independent of qualification, supported API);
  - the remaining-extraction plan.

  The contract, `probe_digest` and the shipped reviewed reference are unchanged.

## Acceptance criteria

- AC1: version 0.12.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.12.0` pushed with `main`. A fresh install from the tag SHA reports 0.12.0, and its shipped
  reference matches the installed probe digest.

## Evidence (2026-10-18)

- AC1: `__version__` 0.12.0; CHANGELOG `## 0.12.0 — 2026-10-18`; README pins `@v0.12.0`.
- AC2:
  - `scripts/check-wheel.sh` on the release content (`af24877`): 442 tests OK, 2 skipped.
  - CI for `af24877` failed on its whitespace check. The cause was a trailing blank line in
    `docs/specs/release-0.11.0/packet.md`, carried over from the 0.11.0 evidence commit. `1c569f2` removes it, and
    CI for `1c569f2` is a success.
- AC3: `refs/tags/v0.12.0` = `eb06b0c4` → commit `1c569f20aa30a680c025b7a305cf50ccb1735cd0`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag reports `agent-harness 0.12.0`.
  - `state_snapshots` and `eval_runner` import.
  - `reviewed_reference()` passes, and the shipped reference carries the installed probe digest
    (`sha256:ee87222a…`).
