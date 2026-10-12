# Showcase SDD-OBS-001: T-003 dependency conflict, a stale T-002 base (2026-10-12)

- Scope: Harness analysis of the failed `start T-003` (dependency merge conflict between T-001 `06d7f5d` and T-002
  `238029e`) and the supported route forward. The analysis is a read-only reading of the repository, the lifecycle
  state and `harness.py`.
- Verdict: **do not resolve the merge conflict.** It is a symptom. T-002's accepted checkpoint was built and verified
  on pre-incident code, so merging it would accept that T-002 as if it rested on the accepted T-001. T-002's
  completion must be corrected, and the lifecycle defect that caused it must be fixed first.

## What happened

- **T-001 (new epoch).** Claimed at 20:23 UTC, completed at 20:47:16 UTC on 2026-10-11 at `06d7f5d`, which descends
  from `main` (`a24b8ae`).
- **T-002 (new epoch).** Claimed at 20:47:35 UTC, 19 s later, and completed at `238029e`. Its parent is `0462412`
  ("agent released-attempt SDD-OBS-001 T-002", 2026-10-09). That commit is the pre-incident attempt, built on the
  pre-incident T-001 checkpoint `77813d4`. `77813d4` is **not** an ancestor of `06d7f5d`.
- **What T-002's checkpoint therefore contains:**
  - the pre-incident T-001 (that is the source of the conflicts in `tests/test_verify.py`,
    `verification/fingerprint.py` and `verification/planner.py`, which T-001 owns and T-002 never edited);
  - 1,240 changed lines of the pre-incident T-002 attempt (`77813d4..0462412`: store and supervisor);
  - 421 lines of new-epoch work (`0462412..238029e`).
- **The cause.** `prepare_task_worktree` reuses an existing clean task worktree as-is (`if target.exists(): … return
  target`). It never checks that the worktree's HEAD descends from the dependencies' `checkpoint_commit`, or that the
  worktree belongs to the current epoch. The pre-incident T-002 worktree survived the `.agent-state` loss, so the
  new-epoch attempt continued on it.
- **The consequences:**
  - T-002 was verified against T-001 code that is not the accepted T-001;
  - historical attempt work entered the new epoch without revalidation, against the accepted recovery rule
    "imports zero historical attempts";
  - every descendant built on T-002 inherits the stale base.

## Supported route

1. **Fix the lifecycle first.** This is a Showcase maintenance change to `harness.py`, reviewed by Harness like the
   recovery fixes.
   - `prepare_task_worktree` reuses an existing worktree only when its HEAD descends from every dependency's
     `checkpoint_commit` (`git merge-base --is-ancestor`). The same holds for a dependency-free task, whose HEAD must
     descend from the recorded base. Otherwise it fails closed with a distinct error, telling the operator to archive
     and remove the worktree.
   - The completion path (`complete` / checkpoint acceptance) checks the same ancestry for the candidate, so a
     stale-base completion can never be accepted again.
   - Tests: a clean pre-epoch worktree on an older dependency checkpoint is refused at start, and a candidate not
     descending from a dependency checkpoint is refused at completion. Both tests must fail on the current code.
2. **Correct T-002's completion with the purpose-built operation:** `correct-completion T-002 --reason-code
   COMPLETION_SEMANTICALLY_INVALID`, with operator attribution.
   - The defect evidence is a file stating the facts above: the parent chain `238029e → 0462412 → 77813d4`;
     `77813d4` is not an ancestor of the accepted T-001 `06d7f5d`; historical attempt work is included.
   - This keeps T-002's completion record as history and moves it to `correction_required`, so T-003 cannot start on
     it.
   - `reopen T-002` is an acceptable alternative (T-003 is not running). It starts a new attempt instead of a repair,
     so prefer the correction, which states the reason.
3. **Preserve the evidence, then remove the stale workspace.**
   - Point an archive ref at `238029e` (for example `refs/agent-archive/SDD-OBS-001/T-002/attempt-1`), so the
     accepted-then-corrected checkpoint stays reachable.
   - Then remove the T-002 worktree and branch with `worktree-remove`. Do not edit `.agent-state`.
4. **Repair T-002 on the accepted base.** Use the existing repair flow (`authorize-completion-repair`,
   `claim-completion-repair`, `complete-repair`) in a fresh worktree created from `06d7f5d`; with step 1 in place,
   nothing else is accepted.
   - T-002's intended changes may be ported from `0462412..238029e`, and the pre-incident attempt may serve as a
     reference. All of it is new-epoch work, verified fresh by T-002's own verification command and evaluated
     independently.
   - T-001's files come from the base, so the three conflicts disappear.
5. **Then start T-003 on the replanned contract.** Its dependency merge is then a plain descent: T-002's new
   checkpoint descends from T-001's. `.agent-runs/SDD-OBS-001-T-003-attempt-1/pre-replan.patch` (sha256
   `72f7c726…`) is reference material only; its runner change must pass the replanned verification like any new
   work.

"Preserving both accepted checkpoints" therefore means:
- **T-001 `06d7f5d`:** stays accepted, unchanged.
- **T-002 `238029e`:** stays preserved as corrected history, under the archive ref and in the correction record. It
  is not kept as a dependency base, because it never descended from the accepted T-001.

## Also check

T-003 is the only task that has tried to start on T-002. Before step 2, confirm that no other new-epoch task reused
a pre-incident worktree. Every completed task's `checkpoint_commit` must descend from its dependencies'
checkpoints, and a task without dependencies must descend from the epoch's base. A one-off read-only script over the
state and `git merge-base --is-ancestor` is enough.

Checked by Harness (read-only, 2026-10-12). The only completed tasks are T-001 and T-002:
- **T-001 `06d7f5d`:** descends from the epoch base `a24b8ae`.
- **T-002 `238029e`:** descends neither from T-001's checkpoint nor from the epoch base.

So T-002 is the only affected completion. Re-run the check after step 4, and before any later task completes, until
step 1's guard is in place.

## Review of the stale-base fix (Showcase, uncommitted `harness.py` and `test_harness.py`, 2026-10-12)

Verdict: **accept with one required change.** Once it is made, proceed with steps 2–5 of the route.

Verified:
- **Worktree reuse.** `prepare_task_worktree` reuses a clean existing worktree only when its HEAD descends from every
  dependency's `checkpoint_commit`, checked with `git merge-base --is-ancestor`. A dependency-free task must descend
  from `base_commit` instead.
  - The check runs after the dirty-worktree and protocol checks.
  - A rejected worktree is left untouched.
  - Both start paths that reuse a worktree go through this function.
- **Completion.** `cmd_complete` and `cmd_complete_repair` apply the same check to the checkpoint before any record
  is written.
- **Missing objects.** A missing candidate or required commit fails closed with distinct `…_BASE_INVALID` and
  `…_BASE_UNAVAILABLE` codes.
- **Tests.**
  - In Showcase's checkout the three new tests pass: stale reuse refused, valid reuse kept, stale completion
    refused.
  - Mutation check: against `HEAD`'s `harness.py` without the fix, both refusal tests fail and the valid-reuse test
    still passes. So the tests really exercise the guard.
- **Real state.** The read-only ancestry check of all completed tasks (above) is what this guard now enforces at
  start and at completion.

Required change:
- **R1. An unknown base must fail closed.** For a dependency-free task, `task_base_commits` returns `[]` when
  `base_commit` is missing or invalid. Both the reuse check and the completion check then pass without checking
  anything.
  - `stage_first_task_base` already refuses an existing worktree that has no recorded base, so this is reachable
    only with damaged or legacy state.
  - Still, the guard must not depend on that: raise `…_BASE_UNAVAILABLE` instead of returning `[]`.

Non-blocking:
- **Test the repair path.** Add a test that `complete-repair` refuses a stale checkpoint. The code path exists; only
  normal completion is tested.
- **Test a dependency-free task.** Add a case where an existing worktree's HEAD does not descend from `base_commit`.

## Review of the T-002 repair checkpoint `b6a023c` (2026-10-12)

Verdict: **accept.** T-002 may complete through `complete-repair`, and T-003 may start afterwards.

Verified independently:
- **The guard.** `805d5bd` makes a dependency-free task with a missing or invalid `base_commit` fail closed (R1
  resolved). `70819de` records the correction evidence: the chain `77813d4 → 0462412 → 238029e`, plus an
  `--is-ancestor` check of `77813d4` against `06d7f5d` that exits 1.
- **Archive.** `refs/archive/showcase/SDD-OBS-001/T-002/238029e…` points at `238029e`.
- **Ancestry.** `b6a023c`'s parent is `06d7f5d`, the accepted T-001 checkpoint.
- **Scope.** `06d7f5d..b6a023c` changes only files within T-002's `allowed_paths`: the store, executor, supervisor,
  command, sandbox and their tests. No T-001-owned file is touched.
- **Fidelity of the port.** Compared with the corrected `238029e`, T-002's files are byte-identical except one line in
  `verification/store.py`: the registered `build.gradle` input hash goes from `47c36c…` (the pre-incident
  `build.gradle`) to `4e6329…`, the accepted T-001's `build.gradle`. All 41 pinned trusted-input hashes in
  `store.py` match the files at `b6a023c`.
- **Fresh verification, rerun by Harness.** T-002's verification command was run on an export of `b6a023c` in
  `python:3.13.16-slim` with cryptography 49.0.0: 130 tests OK.
  - Showcase's evidence (`.agent-runs/SDD-OBS-001-T-002-repair-from-06d7f5d/verification/result.json`) names the same
    command, image digest, Python, cryptography, checkpoint and base. Its stdout and stderr hashes match the stored
    logs, which end in `Ran 130 tests … OK`, with exit code 0.
  - On macOS the module needing cryptography cannot import, because there is no 49.0.0 wheel for this host. That
    matches why the evidence was produced on Linux.
- **Lifecycle.** T-002 is `running`, attempt 1, for the repair. `checkpoint_commit` still names `238029e` until
  `complete-repair` records `b6a023c`.

Notes:
- **Historical work.** The ported content includes the pre-incident attempt's store and supervisor work. That is
  acceptable because it is verified fresh on the accepted base. The completion record should say that the content
  came from `0462412..238029e` and was re-verified at `b6a023c`.
- **Evidence location.** The verification evidence currently lives under `.agent-runs/`. Bind it into the
  completion evidence, as the normal completion flow does, so it is not only in an ignored run directory.
