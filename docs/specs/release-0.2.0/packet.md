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

## Evidence (2026-10-06)

- AC1: `__version__` 0.2.0; CHANGELOG `## 0.2.0 — 2026-10-06`; README pins `@v0.2.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 135 tests OK, 1 skipped (CI-only benchmark); minimal
  consumer from an empty directory against the wheel.
- AC3: `refs/tags/v0.2.0` = `ad561b15` → commit `43bb47c5ed1c6b4c451ed0fcf306b87cdcf3a488`, pushed with
  `main` (`90bc93f..43bb47c`). A fresh CPython 3.13.16 venv installing
  `agent-harness @ git+https://github.com/chrobakpiotr/agent-harness.git@43bb47c…` from an empty directory:
  `agent-harness --version` → `agent-harness 0.2.0`; the minimal consumer runs (scripted launch with sealed
  candidate, process cancel with confirmed drain); the directory stays empty.
- GitHub Actions result: not verified (GitHub CLI not authenticated in this environment).
