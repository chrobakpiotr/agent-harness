# Version 0.8.4 — OpenCode request counts and the Codex reset time

- Status: accepted (release ordered by the owner 2026-10-11). Distribution: annotated Git tag `v0.8.4`; consumers pin
  the tag SHA.
- Scope: AH5-05b consumer requests from agent-benchmark:
  - `AgentCliBackend.request_counts[request_id]`, the number of requests the OpenCode relay forwarded;
  - `error_details` `resets_at` from Codex's `try again at 7:57 PM.`

  `probe_digest` is unchanged, so the shipped reviewed reference is valid.

## Acceptance criteria

- AC1: version 0.8.4, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.4` pushed with `main`; a fresh install from the tag SHA reports 0.8.4 and its shipped
  reference matches the installed probe digest.

## Evidence (2026-10-11)

- AC1: `__version__` 0.8.4; CHANGELOG `## 0.8.4 — 2026-10-11`; README pins `@v0.8.4`.
- AC2: `scripts/check-wheel.sh` on the release commit: 230 tests OK, 2 skipped. CI run 38172861651 for `9138648`: success.
- AC3: `refs/tags/v0.8.4` = `eb0fb27f` → commit `9138648e01605ac62763a11140ac82671f7306d8`, pushed with `main`.
  Fresh CPython 3.13 venv from the tag: `agent-harness 0.8.4`; the shipped reference matches the installed probe
  digest (`sha256:ee87222a…`).
