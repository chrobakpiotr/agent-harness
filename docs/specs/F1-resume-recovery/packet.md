# F1 recovery — invalidating a pending verification resume (behaviour change, for review)

- Status: **candidate implemented, for independent review** (branch `f1-resume-recovery`, on top of F1
  `f1-contract-staleness`). Separate package; required before Showcase adopts F1 (master, 2026-10-24).
- Gap it closes: under F1 a task stopped with a pending verification resume (`resume-verification`) whose contract
  changed is refused by every resume `start` (`VERIFICATION_CONTRACT_CHANGED_SINCE_START`); `fail` and `release` need
  `running`, `human-resolve` needs `escalated`, a failed-task `replan-task` needs a human resolution, and no command
  cleared the pending resume. Existing recovery was therefore not sufficient.

## Decision (master, 2026-10-24)

A separate, small recovery package after F1, no bypass in F1. Recovery explicitly invalidates the pending resume of
the old attempt, keeping its history, reason and evidence; it rewrites no binding and marks nothing complete; a new
start requires the current contract and full validation. The operation checks the attempt's identity under the common
lock and refuses if the state changed since the pending resume it names. A verified way out must exist before F1's
adoption in Showcase.

## Operation

`harness.py invalidate-verification-resume <feature_dir> <task_id> --resume <identity> --reason <text> --by <operator>`
(library `feature_transitions.cmd_invalidate_verification_resume`; the shim adds the wrapper and the parser entry).

- `load_validated` first (full validation, as every lifecycle command).
- Under `locked_state` (the repository verification lock, then the lifecycle lock): the task must be `failed` with a
  `verification_resume_pending` and no active resume; `validate_pending_verification_resume` checks it is this
  attempt's (`task_attempt == attempts`) and equals the latest history item; its identity
  (`sha256:` + canonical JSON SHA-256 of the pending resume) must equal `--resume`. Any difference refuses
  (`VERIFICATION_RESUME_INVALIDATION_REFUSED`, or `VERIFICATION_RESUME_STATE_INVALID`) and writes nothing.
- Effect, in the one `save_state` commit: the latest history item gets `result: invalidated`, `ended_at`, and
  `invalidation: {operator, reason, reason_sha256, at, prior_status}`; `verification_resume_pending` is removed; the
  status becomes `escalated` (a human's decision). Kept: the attempt number, `contract_binding`, `last_failure`,
  `last_failure_evidence`, `last_attempt_commit`, the history. Nothing is completed, nothing starts.
- The F1 refusal of a resume `start` names the way out, with the identity to pass:
  `…; this attempt has a pending verification resume: invalidate it first with invalidate-verification-resume --resume
  sha256:…`.

**Why `escalated`:** the following steps are the existing human path. `human-resolve` requires `escalated`; it
authorizes a retry; `replan-task` (failed, after that resolution) gives the task a packet under the refreshed contract
(the packet records the feature fingerprint, which covers the contract); `start` then begins a new attempt under the
current contract and records its binding.

## The way out, command by command (verified end to end)

1. Refresh the contract (authoring and acceptance), commit, `reconcile-feature` (F1 packet, *Refreshing after a
   refusal*).
2. `invalidate-verification-resume … --resume <identity from the refusal> --reason … --by …`.
3. `human-resolve … --decision … --by …`.
4. `replan-task … --expected-status failed …` with the task's planning input; commit it.
5. The stopped attempt's worktree still holds the old contract (`existing task worktree contains stale
   spec/plan/tasks`), and a new worktree needs its branch removed deliberately (`local task branch already exists
   without a worktree`). Keep the attempt's commits reachable first, e.g. `git update-ref
   refs/attempts/<feature>/<task>/<attempt> refs/heads/agent/<feature>/<task>`, then remove the worktree and the
   branch. (`worktree-remove` deletes the branch without keeping its commits: see *Observations*.)
6. `start`: attempt N+1, under the current contract, with its binding.

## Verification (Showcase `ac552f0` with F1 and this package installed)

`migration/showcase-wrapper/tests/test_f1_resume_recovery.py`, 6 tests OK:
- `test_r_a_stuck_resume_is_refused_with_its_way_out`: the refusal names the command and the identity; nothing written;
- `test_r_invalidation_ends_the_resume_and_a_new_start_runs_under_the_current_contract`: steps 1–6; history, reason,
  evidence and the old binding kept, status `escalated`, not ready until the human path, no completion record; attempt
  2 runs with the new binding, and attempt 1's commits stay reachable;
- `test_r_a_resume_other_than_the_one_named_is_refused_without_change` (a changed request time, an unrelated identity);
- `test_r_without_a_pending_resume_nothing_is_invalidated`;
- `test_r_an_inconsistent_pending_resume_is_refused` (history differs from the pending resume);
- `test_r_reason_and_operator_are_required`.

Library: `tests/test_feature_transitions.py` (the new functions are F1's own; the namespace names they read are
declared).

## Observations (not changed here)

- A task without `test_mode`/`test_seam` gets a legacy packet that stores both as `null` while its semantic hash was
  computed without them; a later `replan-task` then fails with `ACTIVE_PACKET_AMBIGUOUS: packet semantic contract
  fingerprint is inconsistent`. Independent of F1 (also without any contract change); the tests use tasks with both
  fields, as Showcase's replan tests do.
- `worktree-remove` deletes the task branch with the attempt's checkpoint commits. The state keeps the commit ids
  (`last_attempt_commit`), but unreferenced commits can be garbage-collected. For decision: keep an archive ref in
  `worktree-remove`, or document the manual step (as above).
