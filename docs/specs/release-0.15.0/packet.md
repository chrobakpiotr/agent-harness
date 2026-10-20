# Version 0.15.0 — verification-contract validation (AH5-13)

- Status: **released** 2026-10-20 on the owner's order. Distribution: annotated Git tag `v0.15.0`; consumers pin the tag
  SHA.
- Scope: AH5-13 `agent_harness.verification_contract` (`docs/specs/AH5-13/packet.md`). Extra difference from 0.14.0:
  Showcase's portability fix in the library-owned `migration/showcase-wrapper/tests/test_runner_launch.py` (test only).
  The contract, `probe_digest`, the shipped reviewed reference and the supported API are unchanged. No behaviour
  change; `v0.14.0` is not moved.

## Acceptance criteria

- AC1: version 0.15.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.15.0` pushed with `main`. A fresh install from the tag SHA reports 0.15.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-20)

- **Evaluation:** pass with amendments at `e4322be` (`docs/specs/AH5-13/packet.md`); amendments applied in `4e0ae10`.
- **Rehearsal** (`rehearsal-13`, Showcase `fd96eeb`, `BASE_PINNED=1` so the base side runs Showcase's own v0.14.0
  pin `cc9d33a`, `JOBS=4`; library `e4322be`):
  - Host window 2026-10-20T07:45:28Z to 08:31:23Z, load about 2–7; agent-benchmark had no runs. Showcase's
    `tooling/agent-harness` and HEAD did not change during the run.
  - **CI:** 21 commands; exit codes identical; output identical after masking durations and eval ids.
  - **Tests:** base 921 ok, 1 FAIL, 1 ERROR; wrapped 921 ok, 2 FAIL, 2 ERROR, plus the shim's new binding test
    `test_verification_contract_binds_showcase_layout_and_patches_reach_it` (ok). The one other difference,
    `test_concurrent_sequential_replans_have_one_winner` on the wrapped side, passes alone on both sides (34 s): the
    load- and timing-sensitive test recorded in earlier rehearsals.
  - On both sides, the baseline failures: `test_register_manual_observation_…` and
    `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs` (`docs/specs/AH5-12/packet.md`).
  - The version-pin tests did not differ because the rehearsed library still said 0.14.0. With 0.15.0 they are the
    expected candidate difference and must pass after Showcase's re-pin.
  - **Rollback:** wrapped → base, base → wrapped and base → base all ok.
- **Non-docs difference from the rehearsed `e4322be`:** the amendment tests, one blank line removed from the
  `verification_contract.py` shim, the version (0.15.0) and Showcase's test portability fix. The shim change was
  checked directly: Showcase's `test_verification_contract`, `test_cutover_wrapper` and `test_wayfinder` pass through
  it (30 tests).
- **Wheel:** `scripts/check-wheel.sh` on `9134b1f`: 545 tests OK, 2 skipped. Later commits change only `docs/`.

## Evidence (2026-10-20)

- AC1: `__version__` 0.15.0; CHANGELOG `## 0.15.0`; README pins `@v0.15.0`.
- AC2: `scripts/check-wheel.sh` on the release code (`9134b1f`; `717f4ba` adds only docs): 545 tests OK, 2 skipped.
  CI run 39397347276 for `717f4ba`: success (Offline package checks, Python 3.13).
- AC3: `refs/tags/v0.15.0` = `0e78e92e` → commit `717f4bab01c692963e6b66b680a947b45734226f`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag SHA reports `agent-harness 0.15.0`; `verification_contract` imports, and
    a call without its seams raises `TypeError`.
  - The shipped reference carries the installed probe digest (`sha256:ee87222a…`).
- **Branch protection:** the push of `717f4ba` to `main` was accepted as an admin bypass (no pull request, unsigned
  commits, required checks not run before the push), under the owner's direct-push decision. CI ran after the push
  and passed. The bypass does not mean those protections were satisfied.

## Consumer handoff

| Consumer | Pin now | Adoption of 0.15.0 | Rollback |
|---|---|---|---|
| Showcase | v0.14.0 `cc9d33a` (`89382cd`) | pin `717f4ba`; copy `verification_contract.py` (mode 100755), `tests/test_cutover_wrapper.py` and `tests/test_runner_launch.py` from `migration/showcase-wrapper/`; move the three version-pin tests to 0.15.0; run CI | the whole 0.14.0 set: pin `cc9d33a` and the previous copies |
| agent-benchmark | v0.14.0 `cc9d33a` | not needed: it does not use `verification_contract`, and 0.15.0 changes nothing it uses. A re-pin is optional | — |
