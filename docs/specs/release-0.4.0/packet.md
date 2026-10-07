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

## Evidence (2026-10-07)

- AC1: `__version__` 0.4.0; CHANGELOG `## 0.4.0 — 2026-10-07`; README pins `@v0.4.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 165 tests OK, 1 skipped (CI-only benchmark).
- AC3: `refs/tags/v0.4.0` = `e26b16d1` → commit `092b8d761b6ad4d8b7c2e31765cc273e8c5a573e`, pushed with `main`
  (`5fb2936..092b8d7`). Fresh CPython 3.13.16 venv installing `agent-harness @ git+…@092b8d7…`, empty directory:
  `agent-harness 0.4.0`; `v2-limit-exceeded.json` validates (`oom`, `LIMIT_EXCEEDED`); the minimal consumer runs.
- GitHub Actions for `092b8d7`: completed success.
