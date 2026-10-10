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
