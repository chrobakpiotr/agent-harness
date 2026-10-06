# Version 0.2.0 — offline launch API

- Status: accepted (release ordered 2026-10-06). Distribution: annotated Git tag `v0.2.0`; consumers pin the
  tag SHA. No GitHub Release and no PyPI package until AH5-06.
- Scope: contract v1 (unchanged) and the offline launch/cancel API `agent_harness.execution` (ADR 0004:
  scripted `fake` and controlled local process backends, never qualified). Internal: the verification
  modules moved in AH5-04a-1 and the `grants` extra. Python >= 3.13.
- Consumer: agent-benchmark reviewed ADR 0004 and pins this tag for AB5-06b.

## Acceptance criteria

- AC1: version 0.2.0, CHANGELOG entry, README install line and runnable examples.
- AC2: `scripts/check-wheel.sh` green on the release commit.
- AC3: annotated tag `v0.2.0` pushed with `main`.
