# Showcase SDD-OBS-001: T-900 coverage scope, CI fixture fallout, T-004 restart route (2026-10-12)

- Scope: Harness routes for three items from Showcase after T-003's repair. The analysis is a read-only reading of
  the repository at `main` `c92fe01`, the lifecycle state and the task definitions.
- Routes:
  1. the two exclusion-guard tests and the stale `shutil.which` mock belong in T-900's evaluation scope;
  2. the five CI failures are fixed as a fixtures-only maintenance follow-up to `805d5bd` on `main`;
  3. T-004 is restarted from the accepted dependency checkpoints, after its pre-incident worktree is archived and
     removed.

## 1. T-900 evaluation scope (executable-resolution coverage)

Showcase confirms the Harness finding (4fd5d72): in the existing test both paths lie under one worktree, so either
guard alone produces the refusal. With roots under `pathlib.Path.home()`, a probe reaches each guard independently.

T-900's evaluation must include two separate tests in `test_verification_sandbox.py`, each with roots under
`pathlib.Path.home()` (no world-writable ancestor), and each failing when only its own guard is disabled:

1. **A declared search directory inside an excluded root** (the worktree, the sandbox home or `$TMPDIR`) raises
   `executable-search-path-excluded`. This is the guard in `trusted_executable_directories`.
2. **An executable under an excluded worktree nested inside a trusted search directory** raises
   `executable-path-excluded`. This is the guard in `resolve_executable`'s search loop.

Also in T-900's scope: `test_verification_backend_qualification.py`'s
`test_discovery_does_not_qualify_a_candidate` still mocks `shutil.which`, although discovery uses the shared
resolver. Its result therefore depends on whether Docker is present. Rewrite it against `resolve_executable`.

T-900 owns `test_verification_backend_qualification.py`. `test_verification_sandbox.py` is owned only by T-002 and
T-003, so if T-900 requires the two guard tests there, it does so through a T-003 completion correction and repair.
T-900 must not add them to T-003's files itself.

## 2. CI fixture fallout from the ancestry guard (`805d5bd`)

Agentic SDD CI #38250839389 fails five success-path tests whose fixtures complete a task without a valid
`base_commit` or dependency checkpoint, which the guard now correctly refuses:

| Test | File |
|---|---|
| `test_m4_03_complete_required_vc_proof_accepts_completion` (×3: `CanonicalCompletionRepairTests`, `CompletionCorrectionTest`, `LegacyRepairCheckpointBindingProtocolTests`) | `tests/test_harness.py` |
| `test_m43_41_49_c2_requires_checkpoint_and_keeps_other_validation` | `tests/test_harness.py` |
| `test_unplanned_legacy_completion_retains_existing_behavior` | `tests/test_verification_completion_boundary.py` |

Route: **a fixtures-only maintenance commit on `main`, reviewed by Harness,** as a follow-up to `805d5bd`.
- **This is the same route as the guard.** `805d5bd`, like the recovery fixes before it, was lifecycle maintenance
  on `main`, outside the SDD-OBS-001 task DAG. It already changed `harness.py`, which is owned by T-004 and T-009.
  Task-path ownership governs what a task attempt may change; it does not reserve files on `main` against the
  maintenance that broke them. The fixture fallout belongs to the change that caused it.
- **Content.** Each fixture records a real committed feature base: a commit created in the fixture repository and
  stored as `base_commit`, or as the dependency's `checkpoint_commit`, from which the completed checkpoint descends.
  - No change to `harness.py`.
  - No weakening of `require_task_commit_ancestry`.
  - No skip and no mock of the ancestry check.
- **Verification.** The five tests pass, plus the full `test_harness.py` and `test_verification_completion_boundary.py`
  modules. CI must be green on the commit. Also add one assertion per fixture class that a checkpoint not descending
  from the base is still refused, so the fixtures cannot drift back to vacuous passes.
- **Effect on T-004 and T-009.** Their attempts start from their dependency checkpoints, not from `main`, so they do
  not contain this maintenance (or `805d5bd`). When they are integrated, their `test_harness.py` and
  `test_verification_completion_boundary.py` must keep these fixtures. State that in their handoffs.

Send the diff for review before committing it.

## 3. T-004: restart on the accepted dependencies

Facts:
- In the new epoch T-004 is `pending`, with 0 attempts, no `checkpoint_commit` and no recorded worktree.
- The existing clean worktree at `0019649` ("agent checkpoint SDD-OBS-001 T-004", 2026-10-09) is the
  **pre-incident** attempt. It descends from none of T-001 `06d7f5d`, T-002 `b6a023c` and T-003 `05b63a6`, and it is
  not in `main`'s history.
- `prepare_task_worktree` correctly refuses it (`WORKTREE_STALE_BASE`).

So there is no accepted T-004 checkpoint in this epoch. `0019649` is historical work to preserve, not a checkpoint
to keep building on. Route, the same as for T-002's stale base:

1. **Preserve it.** Create an archive ref:
   `git update-ref refs/archive/showcase/SDD-OBS-001/T-004/0019649<full-sha> 0019649<full-sha>`, matching the T-002
   archive's naming.
2. **Remove the stale workspace** with the lifecycle `worktree-remove` command, not with `git worktree` or `branch -D`
   directly. Check first that it is clean. Do not edit `.agent-state`.
3. **Start T-004 normally.** `start` builds a new worktree from the dependency checkpoints (T-003 `05b63a6` descends
   from T-002 `b6a023c`, so the merge is a plain descent), and the ancestry guard accepts it.
4. **Use the pre-incident content only as reference.** All of it is new-epoch work, verified fresh by T-004's own
   verification and evaluated independently, as with T-002.
5. **No retry grant is needed.** T-004 has used no attempts in this epoch, and removing a stale pre-epoch worktree is
   not a task attempt.
