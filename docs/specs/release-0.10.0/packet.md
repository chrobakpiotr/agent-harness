# Version 0.10.0 — the verification sandbox and workspace (AH5-08a)

- Status: accepted (release ordered by the owner 2026-10-18). Distribution: annotated Git tag `v0.10.0`;
  consumers pin the tag SHA.
- Scope: AH5-08a (`docs/specs/AH5-08/packet.md`). `agent_harness.sandbox` and
  `agent_harness.verification.workspace` move from Showcase `49ab97f`, with three explicit consumer-policy seams.
  The contract, `probe_digest` and the shipped reviewed reference are unchanged.

## Acceptance criteria

- AC1: version 0.10.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.10.0` pushed with `main`. A fresh install from the tag SHA reports 0.10.0, and its shipped
  reference matches the installed probe digest.
