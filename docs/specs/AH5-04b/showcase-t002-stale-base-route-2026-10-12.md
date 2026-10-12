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
