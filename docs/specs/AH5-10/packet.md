# AH5-10 — Move the eval engine

- Status: **accepted**. Step 3, on the owner's order 2026-10-18. Source: Showcase `49ab97f`. The owners decided
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
