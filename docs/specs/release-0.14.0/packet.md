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
