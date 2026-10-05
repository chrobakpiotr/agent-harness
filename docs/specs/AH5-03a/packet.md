# AH5-03a — Installable slice without launch authority

- Status: accepted.
- Source: master plan AH5-03a; depends on AH5-02. Showcase input `50c18f9` (read-only).

## Allowed paths

`src/agent_harness/{machine_outcomes,trust,telemetry}.py`, `src/agent_harness/verification/{__init__,model,serialization}.py`,
`tests/test_{trust,machine_outcomes,telemetry,serialization,boundaries}.py`, `pyproject.toml` (lint scope),
`docs/migration/`, `docs/specs/AH5-03a/`, `AGENTS.md`, `README.md`.

## Scope decision

`telemetry.record_manual` and its CLI need `verification.store` and the lifecycle module (`import harness`
through a `__file__`-based `sys.path` insert), so only the provenance part of `telemetry.py` moves now.
No JSON schema is used by the moved modules; schemas move with their users.

## Acceptance criteria

- AC1: modules copied from the source SHA with only the deviations listed in `docs/migration/provenance.md`.
- AC2: no hard-coded Showcase root: no `__file__`, `Path.cwd`, `getcwd` in library modules (mechanical test).
- AC3: diagnostic/blocked semantics unchanged (origin admission and unqualified backend stay `verification-blocked`).
- AC4: installed wheel, tests outside the checkout, real temporary Git repositories; two consumer repositories
  do not mix outputs.

## Evidence (2026-10-05)

- AC1: `diff` of every module against `git show 50c18f9:…` shows only the listed deviations.
- AC2: `NoLocationDerivedRootTest`; mutation adding `HERE = __file__` to `telemetry.py` → FAIL.
- AC3: `test_origin_admission_stays_blocked`.
- AC4: `scripts/check-wheel.sh`: 35/35 OK on CPython 3.13.16; 34 OK + 1 skipped on 3.9.6;
  `test_two_consumer_repositories_do_not_mix_outputs` uses `git init` repositories.
- `ruff check .` clean (style rules C408/I001/TRY004 scoped off for verbatim `verification/*.py` only).
