# AH5-09 — Move the state snapshots

- Status: **done**, released as v0.12.0. Step 3 of moving the Showcase harness into the library, continued on the owner's order
  2026-10-18. Source: Showcase `49ab97f`.
- Allowed paths:
  - `src/agent_harness/state_snapshots.py`
  - `tests/test_state_snapshots.py`
  - `migration/showcase-wrapper/state_snapshots.py`
  - `migration/showcase-wrapper/tests/test_cutover_wrapper.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-09/`, `docs/specs/release-*/`
  - `pyproject.toml`, `CHANGELOG.md`, `README.md`, `src/agent_harness/__init__.py`

## Findings (2026-10-18)

- `state_snapshots.py` (1177 lines) imports only the standard library. It is generic SDD state machinery:
  - write-ahead snapshots of `.agent-state` under `refs/agent-state*`;
  - the repository-wide lifecycle lock;
  - prepared/current refs;
  - retention and restore.

  Its only caller is `harness.py`.
- `state_recovery.py` (138 lines) is Showcase-specific. It validates one incident (SDD-OBS-001's lost state)
  against `docs/specs/AGENT-STATE-RECOVERY-001/evidence/`, so it stays in Showcase.

## Acceptance criteria

- AC1: the module moves verbatim. The ported tests pass in the library, and any test whose driver stays in
  Showcase is listed.
- AC2: the cutover rehearsal at `49ab97f` gives the 08b results plus the listed AH5-09 differences, and rollback
  passes.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

## Evidence (2026-10-18)

- **AC1.** The module is verbatim. 25 of the 26 tests are ported and pass, plus the library suite; the 26th drives
  Showcase's `harness.py` CLI.
- **AC2.**
  - Rehearsal at library `a6a072f` against Showcase `49ab97f`. Results are in `evidence-rehearsal-09-49ab97f.tar.gz`.
  - The only new failures on the wrapped side are the listed `test_state_snapshots` removal and
    `test_m4_1_18_concurrent_identical_binding_safe`, which passes 3/3 alone on both sides.
  - CI exit codes are identical: A005 timed out at 180 s on both sides under load, and passes alone in 70.6 s.
  - Rollback is ok in all three directions.
  - Showcase's working tree changed during the run (its own v0.11.0 cutover in progress). The rehearsal uses an
    archive of `49ab97f`, so its results are unaffected.
- AC3: see `docs/specs/release-0.12.0/packet.md` and the evaluation below.
