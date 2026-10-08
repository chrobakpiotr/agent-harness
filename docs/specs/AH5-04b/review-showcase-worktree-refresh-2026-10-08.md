# Harness review of Showcase WORKTREE-PROTOCOL-REFRESH-001 and AH5-04B-QUAL-001 T-002 attempt 4 (2026-10-08)

- Scope: read-only review of `c6809f2` (worktree refresh, spec `c53aa40`) and `27f5cd6` (T-002 Q11–Q16 probes,
  branch `agent/AH5-04B-QUAL-001/T-002`, refresh commit `6829309`). Experiments ran in throwaway clones only.
- Verdict: `c6809f2` fails AC-002/AC-004; `27f5cd6` passes its tests with three conditions. T-002 completes after
  conditions 1–2 are fixed in the running attempt; dependent reruns wait for the refresh fix.

## c6809f2 — worktree refresh

| # | Severity | Finding |
|---|---|---|
| 1 | high | The stale check and `git restore` cover the whole active feature dir, but the freshness guard fingerprints only spec/plan/tasks/design/verification-contract/handoff. Dependency output inside the feature dir that root lacks counts as stale and is deleted by the refresh commit; a refresh commit is added even when the fingerprinted files are current. Reproduced: dependency writes `docs/specs/<F>/evidence/T-A.json` → absent in the dependent worktree; the deletion wins without conflict on merge. |
| 2 | medium | Root content not produced by the dependency enters the task branch: later-task evidence committed on root, and uncommitted plus untracked files in the feature dir (`git add -A`). Unlike the cached `base_commit`, the snapshot is re-taken on every creation, so dependents can get different unversioned snapshots. |
| 3 | medium | Path `agent-harness` does not exist at the root (pre-existing, inherited from `execution_base`), so harness code is never refreshed. Do not "fix" it to `tooling/agent-harness`: that would overwrite dependency code in `tooling/agent-harness/qualification/**`. Remove the dead entry. |
| 4 | low | The fail-closed test patches the guard to raise; no test drives a real refresh that still fails the real guard. |
| ok | — | Cleanup removes only what the call created (branch pre-check, `worktree add` failure leaves nothing). Builder changes are measured from the worktree's starting HEAD, so the refresh commit is not counted (AC-003 holds). |

Fix: refresh and compare only the fingerprinted feature files plus `AGENTS.md`, `CLAUDE.md`, `docs/agentic-sdd`,
`.claude/agents`, `.gitignore`; take them from the committed root `HEAD`, failing closed when those paths are dirty;
add a regression test with dependency evidence inside the feature dir and one with no stale files (no refresh commit).

Impact on the real chain: T-002's refresh `6829309` deleted nothing (T-001 wrote nothing in the feature dir; only
`.gitignore`, spec, plan, tasks and verification contract were modified), so T-002's worktree is usable. It did copy
the later tasks' evidence (139 files) into T-002's branch. T-003–T-005 write 74 files under the feature dir, so
recreating T-004…T-900 with `c6809f2` would delete or revert rerun evidence.

## 27f5cd6 — T-002 Q11–Q16

Six focused tests pass in the T-002 worktree; mutations accepting the stale generation or restarting the drained
container are caught.

1. Q12 compares the persisted identity with itself at `generation - 1`; generation is always 1 and no controller
   action takes a caller generation. Make `cancel`/`drain`/recover take the caller's generation, bump the generation
   once, and show a controller holding the old one refused.
2. Q15/Q16 controllers exit normally, and Q16 never attempts a relaunch. The lineage that qualified (`097c960`)
   SIGKILLed the controller and attempted a refused relaunch. Restore both: kill the controller, then attempt the
   relaunch and record the refusal.
3. `q_lifecycle.py` differs from the qualifying lineage by +509/−369 lines, so the 2026-10-08 report
   (`sha256:abeef27d…`) no longer describes the code on the new T-002 lineage. T-003…T-900 and the qualification run
   must be redone before the target is called qualified again.
