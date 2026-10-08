# Harness review round 4: Showcase WORKTREE-PROTOCOL-REFRESH-ROUND4-001 `771ef67` (2026-10-08)

- Scope: read-only review of `b81bb9d..771ef67` (spec `148ddac`, checkpoints `c79f927`, `ef6dfb4`, `d5a7138`,
  `771ef67`); experiments in throwaway clones. Follows
  [`review-showcase-refresh-round3-2026-10-08.md`](review-showcase-refresh-round3-2026-10-08.md).
- Verdict: **pass.** The worktree refresh (`WORKTREE-PROTOCOL-REFRESH-001` plus round 4) is safe to integrate; two
  low follow-ups do not block.

## Evidence

- Suites at `771ef67`: `test_harness` 270, `test_wayfinder` 14, `test_design` 10, `test_verification_contract` 4,
  `test_orchestrate` 5 — all OK; `git diff --check` clean.
- Round-3 finding 1 (lost `base_commit`): probe through the lifecycle without seeding the field — first worktree records
  `base_commit`, a committed plan change is accepted by `reconcile-feature`. At `b81bb9d` the same probe was refused.
- Round-3 finding 2 (`start` drops staged base): `cmd_start` and `cmd_worktree_create` copy `base_commit`/`base_kind`
  back from the staged state and clear the pending pair only on success.
- Round-3 finding 3: the spec (AC-004) now states that a needed refresh restores selected protocol paths from root.
- Mutations against the focused tests: pending-base fingerprint check removed → caught; second `HEAD` read instead of
  the recorded snapshot → caught; `start` not copying the base back → caught.

## Follow-ups (low)

1. The write-ahead `save_state` in `stage_first_task_base` is not pinned: removing it passes all tests, because
   `locked_state` saves in `finally` even on `SystemExit`. It matters only for a hard kill between `git worktree add`
   and the final save; test it with a simulated kill or drop it.
2. Pre-existing (same at `c53aa40`): if the first worktree creation fails before any base is recorded and the feature
   spec is then changed and committed, `reconcile-feature` refuses (`historical base checkpoint is unavailable`) and
   every other command refuses the changed fingerprint; there is no supported recovery. The pending base
   (`pending_base_commit`, proven by `historical_feature_fingerprint`) could serve as the reconcile checkpoint.

## Next for AH5-04B-QUAL-001

Integrate the refresh, then `reopen` T-003 through the lifecycle (its descendants are invalidated), recreate T-003…T-900
worktrees, rerun, regenerate the report and have it independently reviewed. Harness then checks it with
`agent-harness qualification --check`. The 2026-10-08 report (`sha256:abeef27d…`) and the current `completed` statuses
of T-003…T-900 do not establish qualification for the revised T-002 code.
