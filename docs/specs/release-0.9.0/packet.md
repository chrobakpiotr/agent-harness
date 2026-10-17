# Version 0.9.0 — moved modules re-synced with Showcase `49ab97f`

- Status: accepted (release ordered by the owner 2026-10-17). Distribution: annotated Git tag `v0.9.0`; consumers
  pin the tag SHA. This is the pin for the AH5-06b Showcase cutover.
- Scope: AH5-06b-0 (`docs/specs/AH5-06/packet.md`). The verification modules and `telemetry` are re-synced with
  Showcase `49ab97f`, `agent_harness.executables` is new, and the store and authority take
  `issuer_registry_path` and `executable_binder`. The contract, `probe_digest` and the shipped reviewed reference are
  unchanged.

## Acceptance criteria

- AC1: version 0.9.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.9.0` pushed with `main`. A fresh install from the tag SHA reports 0.9.0, and its shipped
  reference matches the installed probe digest.

## Evidence (2026-10-17)

- AC1: `__version__` 0.9.0; CHANGELOG `## 0.9.0 — 2026-10-17`; README pins `@v0.9.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 259 tests OK, 2 skipped.
  - CI run 39071369315 failed on the release commit `afc16f2`. On the hosted runner, `/usr/local/bin`, `/opt` and
    the setup-python tree are mode 777, and `agent_harness.executables` refuses executables under writable
    directories (26 tests). This is the resolver's intended behaviour; Showcase runs the same tests in a container
    with root-owned paths.
  - Fix: `cd4a963` adds a CI step that removes group and world write from those paths. The code is unchanged.
  - CI for `cd4a963`: success.
- AC3: `refs/tags/v0.9.0` = `201f2351` → commit `cd4a963d949f3b44cf3d30ed6ef1415424af057c`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag reports `agent-harness 0.9.0`.
  - `reviewed_reference()` passes, and the shipped reference carries the installed probe digest
    (`sha256:ee87222a…`).
