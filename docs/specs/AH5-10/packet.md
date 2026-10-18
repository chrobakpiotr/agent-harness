# AH5-10 — Move the eval engine

- Status: **done**, released as v0.12.0. Step 3, on the owner's order 2026-10-18. Source: Showcase `49ab97f`. The owners decided
  on 2026-10-07 that the eval engine moves to the library and the suites stay in Showcase.
- Allowed paths:
  - `src/agent_harness/eval_runner.py`
  - `tests/test_eval_runner.py`
  - `migration/showcase-wrapper/eval.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-10/`, `docs/specs/release-*/`
  - `pyproject.toml`, `CHANGELOG.md`, `README.md`, `src/agent_harness/__init__.py`

## Findings (2026-10-18)

- `eval.py` (215 lines) imports only the standard library. Its only consumer coupling is its location: `HERE`,
  `REPO` and `DEFAULT_SUITES = HERE / 'evals'`.
- `design.py`, `wayfinder.py` and `verification_contract.py` import Showcase's `harness` and `runner`, so they
  follow the `harness.py` split; they are not in this packet.

## Acceptance criteria

- AC1: the engine moves with an explicit suites directory and repository anchor. The library tests cover both.
- AC2: through the shim, Showcase's `test_eval` and its eval CLI commands give the same results. The cutover
  rehearsal at `49ab97f` gives the AH5-09 results.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

## Evidence (2026-10-18)

- **AC1.** `test_eval_runner` covers suite validation, the explicit suites directory and anchor, and failure exit
  codes.
- **AC2.**
  - This is a targeted check instead of a second full rehearsal: AH5-09's full rehearsal covers every module except
    `eval.py`, and `eval.py` reaches Showcase only through its CLI and `test_eval`.
  - On the AH5-09 wrapped tree with the shims at `0c01ecd`, all 21 Showcase CI commands give exit codes identical
    to the base run, and `test_eval` and `test_cutover_wrapper` pass. The record is in the AH5-09 evidence archive
    (`wrapped-ci-0910.txt`, `isolated-reruns.txt`).
- AC3: see `docs/specs/release-0.12.0/packet.md` and the evaluation below.
- AC3, independent evaluation of AH5-09 and AH5-10 together: **pass with amendments**. All four amendments are
  applied:
  1. the eval engine no longer falls back to the working directory. An anchor outside Git fails closed, with a
     test and a mutation check, and the source's fallback lives in the Showcase shim;
  2. `test_identical_full_tree_skips_snapshot_commit` is added to the removal list, because it passes vacuously
     through the file-loaded alias, and the guide's alias claim is narrowed;
  3. the Showcase commit-name literal is recorded;
  4. the `origin`-based identity is recorded as a known limitation.

  Routed to Showcase: the suite name and run id in the eval output path are not validated (verbatim, unchanged).
