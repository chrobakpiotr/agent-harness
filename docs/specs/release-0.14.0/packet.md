# Version 0.14.0 — the runner's agent launch on AgentCliBackend (AH5-12b)

- Status: prepared; release on the owner's order, after 0.13.0. Distribution: annotated Git tag `v0.14.0`;
  consumers pin the tag SHA.
- Scope: AH5-12b (`docs/specs/AH5-12/packet.md`). The contract, `probe_digest`, the shipped reviewed reference and
  the supported API names are unchanged. One semantic change: withheld output keeps the process's real outcome.

## Acceptance criteria

- AC1: version 0.14.0, a CHANGELOG entry with the semantic-change note, and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.14.0` pushed with `main`. A fresh install from the tag SHA reports 0.14.0, and its shipped
  reference matches the installed probe digest.

## Before the release

- ~~agent-benchmark's re-check of the evaluation amendments~~: passed on `bf8d085`.
- Closing conditions 1–6 in `docs/specs/AH5-12/packet.md`; publication through a pull request with signed commits.
- A rehearsal against the current Showcase with the 12b shims, and rollback.
- Owner's order to publish.
