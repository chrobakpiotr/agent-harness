# Version 0.13.0 — verification CLI and the runner's checks (AH5-11, AH5-12a)

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.13.0`; consumers pin the
  tag SHA.
- Scope: AH5-11 `agent_harness.verify_cli`; AH5-12a `agent_harness.task_runner`. The contract, `probe_digest`, the
  shipped reviewed reference and the supported API are unchanged.

## Acceptance criteria

- AC1: version 0.13.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.13.0` pushed with `main`. A fresh install from the tag SHA reports 0.13.0, and its shipped
  reference matches the installed probe digest.
