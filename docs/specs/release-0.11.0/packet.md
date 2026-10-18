# Version 0.11.0 — the verification supervisor, command backend and executor (AH5-08b)

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.11.0`; consumers pin the
  tag SHA.
- Scope: AH5-08b (`docs/specs/AH5-08/packet.md`). The supervisor, command backend and executor move from Showcase
  `49ab97f` behind explicit lifecycle, command-policy and retry-control ports. Showcase's retry registry leaves the
  library store. The contract, `probe_digest` and the shipped reviewed reference are unchanged.

## Acceptance criteria

- AC1: version 0.11.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.11.0` pushed with `main`. A fresh install from the tag SHA reports 0.11.0, and its shipped
  reference matches the installed probe digest.
