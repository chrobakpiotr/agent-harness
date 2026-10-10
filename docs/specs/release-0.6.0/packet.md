# Version 0.6.0 — coding-agent CLI backend

- Status: accepted (release ordered 2026-10-10). Distribution: annotated Git tag `v0.6.0`; consumers pin the tag SHA.
  No GitHub Release and no PyPI package until AH5-06.
- Scope: AH5-05b `AgentCliBackend` (`docs/specs/AH5-05b/packet.md`; independent evaluation pass with conditions,
  all fixed; real-CLI smoke test pass). Everything valid under v0.5 stays valid; the contract is unchanged.
- Consumer: agent-benchmark AB5-07 (no-spend pilot).

## Acceptance criteria

- AC1: version 0.6.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit.
- AC3: annotated tag `v0.6.0` pushed with `main`; a fresh install from the tag SHA imports `AgentCliBackend`.

## Evidence (2026-10-10)

- AC1: `__version__` 0.6.0; CHANGELOG `## 0.6.0 — 2026-10-10`; README pins `@v0.6.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 185 tests OK, 1 skipped.
- AC3: `refs/tags/v0.6.0` = `26ce53e1` → commit `93cf6bc6a08453addc8c0eacaa99327f52e02cb6`, pushed with `main`.
  Fresh CPython 3.13 venv from the tag commit, empty directory: `agent-harness 0.6.0`; `AgentCliBackend` imports.
- GitHub Actions: CI for `93cf6bc` (run 38061777751) failed in its whitespace step on
  `docs/specs/AH5-04b/showcase-reopen-fix.patch`. The failure dates from `cd44ed5`; trailing spaces there are the
  patch's literal content. Fixed forward by `b7a28e4` (`.gitattributes`: `*.patch -whitespace`; code identical to the
  tag): CI run 38061896940, success. The tag is not moved.
