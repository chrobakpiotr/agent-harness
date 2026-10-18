# AH5-09 — Move the state snapshots

- Status: **accepted**. Step 3 of moving the Showcase harness into the library, continued on the owner's order
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
