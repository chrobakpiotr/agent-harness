# Version 0.14.0 — the runner's agent launch on AgentCliBackend (AH5-12b)

- Status: prepared; release on the owner's order, after 0.13.0. Distribution: annotated Git tag `v0.14.0`;
  consumers pin the tag SHA.
- Scope: AH5-12b (`docs/specs/AH5-12/packet.md`). The contract, `probe_digest`, the shipped reviewed reference and
  the supported API names are unchanged. One semantic change: withheld output keeps the process's real outcome.

## Acceptance criteria

- AC1: version 0.14.0, a CHANGELOG entry with the semantic-change note, and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.14.0` pushed with `main`. A fresh install from the tag SHA reports 0.14.0, and its shipped
  reference matches the installed probe digest.

## Before the release

- ~~agent-benchmark's re-check of the evaluation amendments~~: passed on `bf8d085`.
- Closing conditions 1–6 in `docs/specs/AH5-12/packet.md`; publication through a pull request with signed commits.
- A rehearsal against the current Showcase with the 12b shims, and rollback.
- Owner's order to publish.

## Pre-release evidence (2026-10-19)

- **Candidate:** `161a25f9b5424c11f0c0883f89d7a3a00cc89b95`, branch `ah5-12b-agent-launch` rebased onto `main`
  `77068b6` (0.13.0 released). Later commits on the branch change only `docs/`.
- **Snapshots and their relation:**
  - `bf8d085`: the evaluated commit `4251a24` plus the evaluation amendments.
  - `0674698`: `bf8d085` plus the 0.14.0 preparation (version, CHANGELOG, README, ownership and cutover rows,
    this packet). The rehearsal ran on it.
  - `4b4eb7c` and `193d8ae`: master's closing conditions. The `--print-command` legacy-preview marker
    (`runner.py`, 6 lines), two Showcase-side tests (preflight with an unavailable sandbox and the re-check at
    launch; the preview marker), and docs.
  - `161a25f`: the rebase onto `77068b6`. `2e54508..77068b6` changes only `docs/`, and the non-docs diff between
    `193d8ae` and `161a25f` is empty.
  - **Non-docs difference from the rehearsed `0674698`:** only the marker and the two tests. Checked directly on
    Showcase `cbbdbca` with the final shims: `py_compile runner.py` ok; `test_runner_launch`, `test_runner`,
    `test_cutover_wrapper` and `test_orchestrate` pass (66 tests). `src/` is identical to the evaluated and amended
    `bf8d085` apart from `__version__`.
- **Wheel:** `scripts/check-wheel.sh` on `161a25f`: 529 tests OK, 2 skipped.
- **Rehearsal** (`rehearsal-0140`, Showcase `cbbdbca`, `BASE_PINNED=1`, `JOBS=4`; library `0674698`):
  - Host window 2026-10-19T19:48:49Z to 20:33:39Z, load about 2 at the start and 3–8 at the end.
  - **CI:** 21 commands; exit codes identical; output identical after masking durations and eval ids.
  - **Tests:** base 901 ok, 2 FAIL, 1 ERROR; wrapped 911 ok, 3 FAIL, 2 ERROR. The differences:
    - the three `test_qualification_report` version-pin tests (they move with the re-pin);
    - the shim's own tests: `test_runner_launch` (10 at `0674698`) and the runner and verify-CLI binding tests;
    - `test_concurrent_sequential_replans_have_one_winner` failed on the **base** side this time and passed on the
      wrapped side; in the 0.13.0 rehearsal it was the other way round, and it passes alone on both sides.
  - **On both sides:** `test_register_manual_observation_serializes_scope_resolution_with_lifecycle_mutation` and
    `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`, the baseline failures in
    `docs/specs/AH5-12/packet.md`.
  - **Rollback:** wrapped → base, base → wrapped and base → base all ok.
  - The live Showcase checkout's `tooling/agent-harness` changed during the run: uncommitted, Showcase applying the
    0.13.0 handoff (the v0.13.0 shims and the `cfd92fc` pin). The rehearsal used the committed `cbbdbca` snapshot.
    Once Showcase commits its 0.13.0 cutover, the 12b shim diff is the same files over a newer base.

## Exact-candidate rehearsal (2026-10-19)

- **Rehearsed:** `rehearsal-0140b` on `d10cca68e6ed6537f999d8f40b04d5a4dbb215ad`. Its code and tests are identical
  to `161a25f9b5424c11f0c0883f89d7a3a00cc89b95`: `git diff 161a25f d10cca6 -- . ':!docs'` is empty. Showcase
  snapshot `cbbdbca`, `BASE_PINNED=1`, `JOBS=4`. Host window 2026-10-19T20:50:28Z to 21:40:58Z, load about 4–12
  (Showcase ran its own tests at the same time; agent-benchmark had no runs).
- **CI:** 21 commands; exit codes identical; output identical after masking durations and eval ids.
- **Tests:** base 901 ok, 2 FAIL, 1 ERROR; wrapped 912 ok, 4 FAIL, 3 ERROR.
  - Expected candidate difference: the three `test_qualification_report` version-pin tests.
  - The shim's own tests, all passing (12 in `test_runner_launch`, and the runner and verify-CLI binding tests).
  - `test_concurrent_sequential_replans_have_one_winner` failed on the wrapped side, and
    `test_conflicting_failed_task_replans_serialize_with_one_winner` on the base side. Rerun alone, both pass on
    both sides (44 s). They are load- and timing-sensitive; in earlier runs each has failed on either side.
  - On both sides: the baseline failures `test_register_manual_observation_…` and
    `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`.
- **Rollback:** wrapped → base, base → wrapped and base → base all ok.

## Showcase's current base (master points 4–5)

- During the run Showcase committed its 0.13.0 cutover: `cbbdbca` → `ea8a54fa38959e255985fd4415dbba599d52c405` →
  `8cfaf08805a82d34dcfb0fc4fbf65c7623125afa`. The rehearsal used the committed `cbbdbca` snapshot.
- **`cbbdbca` → `ea8a54fa`, the agreed migration only:** the pin (`1c569f2` → `cfd92fc`); `runner.py`, `verify.py`
  and `tests/test_cutover_wrapper.py`, byte-identical to the v0.13.0 shims; `PINNED_HARNESS_VERSION` and the three
  version-pin tests (0.12.0 → 0.13.0); `AGENTS.md` (the direct-push decision) and a handover document. The 12b
  rehearsal's wrapped side overwrites the same three shims with the 12b ones, so no behaviour it tested changed;
  the full run is not repeated.
- **`ea8a54fa` → `8cfaf08`:** `AGENTS.md` and the handover document only.
- **Showcase CI:** run 39324499981 passed for `ea8a54fa`; run 39327994011 passed for `8cfaf08`.
  `Dependency review` was **skipped** (it runs on pull requests only) and is not counted as a check performed; it is
  recorded as a CI coverage gap for a later improvement packet, outside this cutover.
