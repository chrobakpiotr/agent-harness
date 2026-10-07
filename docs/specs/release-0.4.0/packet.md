# Version 0.4.0 — contract v2

- Status: accepted (release ordered 2026-10-07). Distribution: annotated Git tag `v0.4.0`; consumers pin the
  tag SHA. No GitHub Release and no PyPI package until AH5-06.
- Scope: contract v2 (ADR 0005; `docs/specs/contract-v2/packet.md`, executed confirmation 150/150 mutants
  killed): request `limits`, result `target` and `limits`, `LIMIT_EXCEEDED`, `contract.validate_target`; v1 documents
  unchanged and valid; offline launch: `ProcessBackend` rejects limits, `ScriptedBackend(fired=…,
  output_truncated=…)`, a qualified backend must state a valid target. Golden fixture `v2-limit-exceeded.json`.
- Consumers: Showcase re-runs B10 of the 04b qualification against v2 results; agent-benchmark adopts v2 when it
  needs `target`/`limits` (and then accepts `LIMIT_EXCEEDED`, which is deliberately not in `ERROR_CODES`).

## Acceptance criteria

- AC1: version 0.4.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit.
- AC3: annotated tag `v0.4.0` pushed with `main`; a fresh install from the tag SHA validates the v2 fixture.
