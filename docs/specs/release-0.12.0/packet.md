# Version 0.12.0 — state snapshots, eval engine, ownership amendment (AH5-09, AH5-10)

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.12.0`; consumers pin the
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
