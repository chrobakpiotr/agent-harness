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
