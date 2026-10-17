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
