# Version 0.8.1 — agent CLI error handling

- Status: accepted (release requested by agent-benchmark and relayed by the owner, 2026-10-11). Distribution:
  annotated Git tag `v0.8.1`; consumers pin the tag SHA.
- Scope: AH5-05b consumer findings on v0.8.0 (`docs/specs/AH5-05b/packet.md`): no candidate for a non-completed run;
  `error_details` with quota classification. `probe_digest` unchanged, so the shipped reviewed reference is valid.

## Acceptance criteria

- AC1: version 0.8.1, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.1` pushed with `main`; a fresh install from the tag SHA reports 0.8.1 and its shipped
  reference still matches the installed probe digest.
