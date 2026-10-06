# Version 0.3.0 — qualification report and shared-contract copies

- Status: accepted (release ordered 2026-10-06). Distribution: annotated Git tag `v0.3.0`; consumers pin the
  tag SHA. No GitHub Release and no PyPI package until AH5-06.
- Scope: contract v1 qualification report (`validate_qualification_report`, `qualification_passes`,
  `validate_capability_binding`, `verify_qualification_evidence`, `qualification_digest`, `review_subject`) with
  mandatory checks Q01–Q16 and B1–B10 and the CLI `agent-harness qualification` (AH5-04b-r, final check pass);
  CLI `agent-harness constitution` and constitution 1.0.1 (AH5-00b); ADR 0001 accepted with amendments; ADR 0005
  (contract v2 shape) proposed. Contract v1 request/result unchanged; `agent_harness.execution` unchanged.
- Consumers: Showcase runs the 04b qualification with the `qualification` command; agent-benchmark can adopt a
  verbatim constitution copy and `constitution --check`.

## Acceptance criteria

- AC1: version 0.3.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit.
- AC3: annotated tag `v0.3.0` pushed with `main`; a fresh install from the tag SHA runs both new commands.

## Evidence (2026-10-06)

- AC1: `__version__` 0.3.0; CHANGELOG `## 0.3.0 — 2026-10-06`; README pins `@v0.3.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 149 tests OK, 1 skipped (CI-only benchmark).
- AC3: `refs/tags/v0.3.0` = `46a85c6a` → commit `0ef88c5b5751e18b88a9f7b454823c7bf3012e0b`, pushed with `main`
  (`cb292c8..0ef88c5`). Fresh CPython 3.13.16 venv installing `agent-harness @ git+…@0ef88c5…`, from an empty
  directory: `agent-harness 0.3.0`; `constitution --digest` → `constitution 1.0.1 sha256:15cecfb8…dcb04`;
  `qualification --check` on the shipped example → exit 2 (its evidence is not shipped, as intended); the minimal
  consumer runs.
