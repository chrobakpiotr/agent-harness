# Version 0.17.0 — lifecycle state access (AH5-14b-1)

- Status: **released** 2026-10-24 on the owner's order. Distribution: annotated Git tag `v0.17.0`; consumers pin the tag
  SHA.
- Scope: AH5-14b-1 `agent_harness.feature_access` (`docs/specs/AH5-14/packet.md`). The contract, `probe_digest`, the
  shipped reviewed reference and the supported API are unchanged. No behaviour change.

## Acceptance criteria

- AC1: version 0.17.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.17.0` pushed with `main`. A fresh install from the tag SHA reports 0.17.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-24)

- **Evaluation:** pass with amendments at `c3a5630`; the amendments (tests for four refusals, the incident hook
  documented, a corrected provenance claim) are applied in `df73de6`. They change only tests and docs: `src/` and
  `migration/` are identical to the rehearsed `c3a5630`.
- **Rehearsal** (`rehearsal-14b1`, library `c3a5630`, Showcase `1b22802d52f59c1629e20edbc7ec7aaf09761d79`, its
  0.16.0 adoption, base on its pin `fc4ad64`, `JOBS=4`): host window 2026-10-23T22:06:52Z to 22:48:54Z, load about
  3–9.
  - **CI:** 21 commands; exit codes identical; output identical after masking durations and eval ids.
  - **Tests:** base 921 ok, 2 FAIL, 1 ERROR; wrapped 922 ok, 1 FAIL, 1 ERROR. The only difference is
    `test_concurrent_sequential_replans_have_one_winner`, failing on the base side this time: the load- and
    timing-sensitive test recorded in every rehearsal. On both sides, the baseline failures
    `test_register_manual_observation_…` and `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`.
  - The version-pin tests did not differ because the rehearsed library still said 0.16.0; with 0.17.0 they are the
    expected difference and must pass after Showcase's re-pin.
  - **Rollback:** wrapped → base, base → wrapped and base → base all ok.
- **Open for master (not in this release):** whether the incident hook should also refuse a result that is neither
  `None` nor a non-empty dict (a behaviour change from the source). F1 stays a separate package; AH5-14c stays blocked.
- **Consumers:** Showcase adopts with the `harness.py` shim (behaviour-identical). agent-benchmark does not use this
  module.
- **Wheel:** `scripts/check-wheel.sh` on `d2b76e6`: 594 tests OK, 2 skipped. Later commits change only `docs/`.

## Evidence (2026-10-24)

- AC1: `__version__` 0.17.0; CHANGELOG `## 0.17.0`; README pins `@v0.17.0`.
- AC2: `scripts/check-wheel.sh` on the release code (`d2b76e6`; `137b0ba` adds only docs): 594 tests OK, 2 skipped.
  CI run 40074949704 for `137b0ba`: success (Offline package checks, Python 3.13).
- AC3: `refs/tags/v0.17.0` = `358c793e` → commit `137b0ba057c4d69c94fc1d8b04b0af8d3b0b039e`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag SHA reports `agent-harness 0.17.0`; `feature_access` imports, and
    `StateAccess()` without its fields raises `TypeError`.
  - The shipped reference carries the installed probe digest (`sha256:ee87222a…`).
- **Branch protection:** the push of `137b0ba` to `main` was accepted as an admin bypass (no pull request, unsigned
  commits, required checks not run before the push), under the owner's direct-push decision. CI ran after the push
  and passed. The bypass does not mean those protections were satisfied.

## Consumer handoff

| Consumer | Pin now | Adoption of 0.17.0 | Rollback |
|---|---|---|---|
| Showcase | v0.16.0 `fc4ad64` (`1b22802`) | pin `137b0ba`; copy `harness.py` (mode 100755) from `migration/showcase-wrapper/` (the other shims are unchanged since 0.16.0); move the three version-pin tests to 0.17.0; run CI | the whole 0.16.0 set: pin `fc4ad64` and the previous `harness.py` |
| agent-benchmark | v0.14.0 `cc9d33a` | not needed: it does not use `feature_access`; `contract`, `execution` and `qualification` are unchanged | — |
