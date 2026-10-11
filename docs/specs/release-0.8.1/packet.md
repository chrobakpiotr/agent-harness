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

## Evidence (2026-10-11)

- AC1: `__version__` 0.8.1; CHANGELOG `## 0.8.1 — 2026-10-11`; README pins `@v0.8.1`.
- AC2: `scripts/check-wheel.sh` on the release commit: 220 tests OK, 2 skipped. CI run [exited with code 0] for `8db36fa`: success.
- AC3: `refs/tags/v0.8.1` = `b5458fdf` → commit `8db36faf1cebdb399bc07effc947d850736df323`, pushed with `main`.
  Fresh CPython 3.13 venv from the tag commit: `agent-harness 0.8.1`; the shipped reference matches the installed
  probe digest.
