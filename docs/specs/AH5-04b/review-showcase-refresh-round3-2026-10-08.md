# Harness review round 3: Showcase worktree refresh `b81bb9d` and T-002 `e37b09c` (2026-10-08)

- Scope: read-only; experiments in throwaway clones. Follows
  [`review-showcase-refresh-and-t002-round2-2026-10-08.md`](review-showcase-refresh-and-t002-round2-2026-10-08.md).
- Verdict: T-002 conditions closed. `b81bb9d` closes every round-2 finding but fails on one new regression: lifecycle
  state no longer records `base_commit`, so `reconcile-feature` is refused for new features.

## T-002 `e37b09c`

Both round-2 mutations are now caught: `refused = relaunch_attempted` (relaunch without the drained tombstone) and
`controller_killed: True` (kill outcome unchecked). T-002 is completed, attempt 4, checkpoint `e37b09c`.

## b81bb9d

Suites at `b81bb9d`: `test_wayfinder` 14, `test_design` 10, `test_verification_contract` 4, `test_orchestrate` 5,
`test_harness` 266 — all OK; `git diff --check` clean.

Round-2 findings, probed:

| Round-2 finding | Status |
|---|---|
| Committed-`HEAD` rule hit design/wayfinder/verification-contract callers | fixed: those callers keep the synthetic snapshot; `test_wayfinder` passes |
| Dependency protocol edits reverted when nothing is stale | fixed: current checkpoint → no refresh commit, dependency `.gitignore` and `docs/agentic-sdd` kept |
| No real-guard test | fixed: an ignored `design.json` makes the real guard refuse after a real refresh; worktree and branch removed |
| Dirty/ignored selected root files | fail closed with cleanup |

New:

| # | Severity | Finding |
|---|---|---|
| 1 | high (regression; fails closed) | `prepare_task_worktree` no longer calls `execution_base`, and nothing else persists `state['base_commit']` (`design`, `wayfinder`, `verification_contract` pass a throwaway `{}`). `reconcile-feature` requires it (`FEATURE_REPLAN_REJECTED: historical base checkpoint is unavailable`). Probe: a new feature, first-task worktree, then a committed plan change → `c53aa40`: `base_commit` set, reconcile ok; `b81bb9d`: `base_commit` `None`, reconcile refused. Existing tests set `base_commit` by hand, which hides it. AH5-04B-QUAL-001 already has `base_commit` `60006b1…` and is unaffected. |
| 2 | medium (pre-existing) | `cmd_start` prepares the worktree on `staged_state` and copies back only the task entry, so a `base_commit` recorded during `start` is dropped even at `c53aa40`; only `worktree-create` persisted it. |
| 3 | low | When a refresh is needed, all protocol paths are restored from the root, so dependency-authored `.gitignore`/`docs/agentic-sdd` changes are reverted (a dependency-only file is deleted). No AH5-04B-QUAL-001 task edits those paths; state the rule in the spec. |

Fix: in `prepare_task_worktree`, for a task without dependencies, record the committed-`HEAD` snapshot as
`state['base_commit']` (`base_kind` `head`) when unset, as `execution_base` did; copy `base_commit`/`base_kind`
from `staged_state` back in `cmd_start`; add a test that creates the first worktree through `worktree-create` and
`start` without setting `base_commit` by hand and then runs `reconcile-feature`; note finding 3 in the spec.

## For AH5-04B-QUAL-001

Finding 1 does not touch this feature's state, and the refresh behaviour that matters for the reruns (dependency
evidence kept, no unnecessary refresh, committed-`HEAD` snapshot, fail-closed cleanup) passes. Recreating T-003…T-900
can start once round 4 lands; the fix is small and should land before more features use the new code. The
2026-10-08 report (`sha256:abeef27d…`) stays stale until the reruns and an independent report review pass.
T-003…T-900 still show `completed` on the old T-002 lineage; invalidate them through the lifecycle (`reopen` of T-003
marks its descendants), not by editing state.
