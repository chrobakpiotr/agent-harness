# Version 0.13.0 — verification CLI and the runner's checks (AH5-11, AH5-12a)

- Status: **released** 2026-10-19 on the owner's order. Distribution: annotated Git tag `v0.13.0`; consumers pin the
  tag SHA.
- Scope: AH5-11 `agent_harness.verify_cli`; AH5-12a `agent_harness.task_runner`. The contract, `probe_digest`, the
  shipped reviewed reference and the supported API are unchanged.

## Acceptance criteria

- AC1: version 0.13.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.13.0` pushed with `main`. A fresh install from the tag SHA reports 0.13.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-19)

- **Candidate:** `2e54508ae6810e736cafcc7a1862acc3d351703a` (code and tests). Later commits on the branch change only
  `docs/`.
- **Evaluation:** attempt 2 passed with amendments (at `daecf4e`; `docs/specs/AH5-11/packet.md`). A short re-check
  of `daecf4e` → `2e54508` passed. `scripts/check-wheel.sh`: 515 tests OK.
- **Rehearsal against the current consumer** (`rehearsal-0130`, Showcase `cbbdbca`, `BASE_PINNED=1`, `JOBS=4`):
  - The library installed on the wrapped side is identical to `2e54508`, and its shims are the candidate's. The
    base side runs Showcase's own pin, v0.12.0 (`1c569f2`). Showcase's `tooling/agent-harness` and HEAD did not
    change during the run.
  - Host window 2026-10-19T07:48:56Z to 19:20:35Z, load about 2–4 at the start and the end. The run was paused
    (SIGSTOP) during agent-benchmark's grading windows, at its request, and then resumed. Both sides kept the same
    snapshots across the pause.
  - **CI:** 21 commands from Showcase's workflow; exit codes identical; output identical after masking durations and
    eval ids.
  - **Tests:** base 901 ok, 2 FAIL, 1 ERROR; wrapped 899 ok, 5 FAIL, 3 ERROR. The differences, all explained:
    - the three `test_qualification_report` version-pin tests (`0.12.0` expected, `0.13.0` found). They move with
      the re-pin;
    - `test_concurrent_sequential_replans_have_one_winner` failed on the wrapped side only. Rerun alone, it passes
      on both sides (23 s each): it is load- and timing-sensitive, and the run was paused while it ran;
    - two extra passing tests on the wrapped side, the shim's own runner and verify-CLI binding tests.
  - **On both sides, so not 0.13.0 regressions:**
    - two `HarnessTest` concurrency tests, which pass alone on both sides;
    - `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`
      (`SECRET_BEARING_CANDIDATE_UNSEALABLE`), the known host-specific fixture error recorded in
      `docs/specs/AH5-06/packet.md`.
  - **Rollback:** wrapped → base, base → wrapped and base → base all ok.
- **Limits:** the full suite ran under a paused, then resumed, schedule; timing-sensitive tests were judged by
  rerunning them alone.

## Evidence (2026-10-19)

- AC1: `__version__` 0.13.0; CHANGELOG `## 0.13.0`; README pins `@v0.13.0`.
- AC2: `scripts/check-wheel.sh` on the release commit `cfd92fc`: 515 tests OK, 2 skipped. CI for `cfd92fc`:
  success (Offline package checks, Python 3.13).
- AC3: `refs/tags/v0.13.0` = `35bccd17` → commit `cfd92fcfb0c572b1b462d605709e7be8b7801b71`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag SHA reports `agent-harness 0.13.0`.
  - `verify_cli` and `task_runner` import.
  - The shipped reference carries the installed probe digest (`sha256:ee87222a…`).
