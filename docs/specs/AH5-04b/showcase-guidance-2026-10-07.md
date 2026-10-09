# Harness guidance to Showcase — runner output schema and 04b DAG placement (2026-10-07)

- Status: guidance. Both subjects are Showcase-owned (ADR 0001: `runner.py`, task-result schema, the SDD
  lifecycle and DAG stay in Showcase); Showcase decides and implements through its own task protocol.
- Input: Showcase update 2026-10-07 (commits `9722223`, `cce5583`): T-001 escalated because Codex CLI 0.160 rejects
  the canonical task-result schema (`additionalProperties: true`, then `uniqueItems`); adding T-010 to SDD-OBS-001
  invalidated T-009's packet (`TASK_REPLAN_REQUIRED`).

## 1. Provider structured output: projection, canonical validation

Accept a provider-specific strict output schema on these conditions:

1. **Generated, never hand-written.** The projection is derived mechanically from the unchanged canonical
   task-result schema, per provider and CLI version: close objects (`additionalProperties: false`), require every
   declared field, drop only constructs the provider rejects (e.g. `uniqueItems`). The canonical schema is never
   edited for a provider.
2. **Canonical validation decides.** Every provider output is validated against the canonical schema afterwards;
   every dropped constraint is enforced there, so a violation is a contract failure, never a pass.
3. **Tested both ways.** For each projection: known-valid canonical results stay valid under it, and inputs
   breaking each dropped constraint are rejected by the canonical step.
4. **Provenance and fail closed.** The run record carries provider, CLI version and projection digest. A schema
   that cannot be projected without losing a required constraint leaves the task blocked for that provider; no
   silent fallback to unstructured output.

## 2. Placing 04b without invalidating T-009

Recommended: a **separate Showcase feature for target qualification** with its own small DAG — Docker Desktop
prototype and full run (Q01–Q16, B1–B10), GitHub-hosted runner fallback, independent review — outside
SDD-OBS-001. 04b qualifies a target; it does not change SDD-OBS-001's origin rules, so SDD-OBS-001's DAG and T-009's
packet stay untouched. 04c depends on 04b's passing qualification report (checked with
`agent-harness qualification --check … --job-id …`), not on a task inside SDD-OBS-001.

Alternative, only if Showcase's protocol allows it: T-009 has never been claimed and SDD-OBS-001 has no persisted
lifecycle state, so re-materialising its packet after an accepted DAG change affects no running attempt. If chosen,
Harness re-reviews only the diff against the reviewed `packet_sha256` `8a9778a1…`. Never bypass the packet guard.

## 3. Replanning a human-resolved failed task (T-003 blocker)

Gap (Showcase `harness.py` at `ac4d94d`, read-only): `replan-task` CAS-binds status, attempts, active packet
revision and contract fingerprint but accepts only `expected_status == running`; `claim`/`start` call
`write_packet`, which refuses a legacy packet whose semantic contract changed before the task can become running.
`write_packet` already prefers an active replanned revision, so the missing piece is only a way to create one for a
failed task. `human-resolve` sets `failed`; `authorize-retry` grants an attempt but does not change the packet;
`reopen` needs a completed task. No existing command sequence is safe; do not delete or overwrite the packet, reset
the feature or edit state.

Proposed Showcase-owned transition (a Showcase task with tests; Harness does not change `harness.py`):

1. Extend `replan-task` to accept `--expected-status failed` only when the task's last state change is a recorded
   human resolution, under the same CAS as today: expected status, attempts, active packet revision, semantic
   contract sha256, plus the feature generation.
2. Publish the new immutable revision into `.agent-state/packet-revisions/`, make it the active revision and append
   the supersession to the lineage in one state replacement; the legacy packet bytes and every prior revision stay as
   history. No running attempt exists, so nothing is terminated; the attempt count and authorization history are
   unchanged.
3. The task stays `failed`; the next attempt needs `authorize-retry` bound to the new revision and fingerprint, then
   the normal claim/start, which resolve the active revision.
4. Tests (red first): a replan of a failed task without a human resolution, a stale revision/fingerprint/attempt
   count/feature generation, a completed task and a concurrent second replan are refused; a valid replan leaves the
   legacy packet byte-identical and the attempt count unchanged; claim/start then use the new revision.

## 4. Safe reopen: descendants from the validated DAG (T-003)

Cause (Showcase `harness.py` at `dbe3c91`, read-only): `cmd_reopen` builds
`{tid: active_task_contract(...) for tid in idx}` for **every** task only to compute descendants; `active_task_contract`
validates each legacy packet file, so one unrelated completed task whose historical packet does not resolve aborts
the reopen (`ACTIVE_PACKET_AMBIGUOUS`).

Fix: [`showcase-reopen-fix.patch`](showcase-reopen-fix.patch) (applies cleanly to `dbe3c91`; Showcase applies it
through its own task — Harness does not change `harness.py`). New `reopen_dependency_graph`: a task with a replanned
revision (`active_packet_revision` set) still resolves its active contract, because a revision may change
`depends_on` (fail closed); a legacy task uses the validated current DAG entry, which is what `active_task_contract`
already returns for it — its packet file is not read. Packet bytes, completion evidence and attempt histories are
untouched; descendants are invalidated exactly as before.

Evidence on throwaway exports: new test `test_reopen_ignores_unrelated_legacy_packets` (reopen T-900 while the
unrelated completed T-001 has a malformed legacy packet) fails on `dbe3c91` with `ACTIVE_PACKET_AMBIGUOUS` and passes
with the patch, leaving T-001's packet byte-identical; the three existing reopen tests pass on both.

## 5. Stale task worktree on retry (AH5-04B-QUAL-001 T-002, 2026-10-08)

State (Showcase `harness.py` at `ba483a4`, read-only): T-002 is `failed`, attempts 3, active revision
`sha256:163acaae…7c`; its worktree is clean at `097c960` with feature fingerprint `7b1b5377…`, while the root
feature is `e67ff091…`, so `start` refuses (`existing task worktree contains stale spec/plan/tasks`) before consuming
the retry grant. `097c960` is reachable from the T-003…T-900 branches, so removing the T-002 branch loses no history.

Gap: `worktree-remove` then `start` is the only existing path, and it is not safe. `prepare_task_worktree` creates
the new worktree from the dependency checkpoint (T-001 `218bfa1`, fingerprint `7b1b5377…`, the old spec) and runs
`assert_worktree_protocol_current` only when reusing an existing worktree, never on a fresh one. The retry would
silently run against the stale spec.

Proposed Showcase task (red first; Harness does not change `harness.py`):

1. Fail closed: call `assert_worktree_protocol_current` on a freshly created worktree too; on failure remove the
   worktree and branch it just created. Test: dependency checkpoint with an old feature fingerprint → refused, no
   attempt consumed.
2. Supported refresh: when the base's feature fingerprint differs from the root, add one commit on the new task
   branch that sets the protocol and feature paths to the root's versioned content (the same path set
   `execution_base` uses), then assert freshness. Test: the worktree matches the root fingerprint, dependency code
   is unchanged, and the dependency checkpoints are ancestors of the branch.
3. Then for T-002: `worktree-remove docs/specs/AH5-04B-QUAL-001 T-002`, then `start`, which consumes the existing
   grant on the new revision. No `.agent-state` edits.

## 6. T-006 / T-900 ordering after the rerun (AH5-04B-QUAL-001, 2026-10-08)

Rerun report (T-005 `0d9deda`, `evidence/docker-desktop/local-20261008T205033Z-7939417fefb5/`), checked with Harness
`v0.5.0`: valid, 26/26 checks `pass`, `independent_review` null → does not pass; qualification digest and review subject
`sha256:64e41e36…4170e`; capability `qualified: false`, refusal `NOT_QUALIFIED`. The top-level
`evidence/docker-desktop/{report,capability}.json` and `job-id.txt` (job `…-2026-10-07-run1`) are stale and do not
verify; do not cite them.

Recommendation: keep the DAG. Reordering T-900 before T-006 is a feature-level change to `tasks.json`; it changes the
feature fingerprint and puts the completed T-001…T-005 packets through replan again.

1. T-006 decides the fallback on the target's checks only: all 26 pass on Docker Desktop, so it records
   `NOT_REQUIRED_PENDING_REVIEW` bound to the report digest above, and states that neither Docker nor GitHub is
   qualified by it. This is the reading the first run used. Interpreting "Docker passes" this way is Showcase's
   decision; record it in the T-006 evidence.
2. T-900 reviews the Docker report and writes `independent_review`; Harness then runs
   `agent-harness qualification --check … --job-id local-20261008T205033Z-7939417fefb5`.
3. If T-900 rejects the Docker report, `reopen` T-006 (this invalidates T-900) and run the GitHub fallback in a fresh
   job. Dispatching it needs the workflow pushed, which needs an explicit human push decision.
4. T-900's registered verification still names the missing `check_selected_report.py`. `replan-task` accepts only
   `running`/`failed` tasks, so the existing supported path is the first run's: start T-900, its verification fails,
   `human-resolve`, `replan-task --expected-status failed` with the recorded proposal (pinned Harness CLI), then
   `authorize-retry` and retry.

## 7. SDD-OBS-001 T-004 / T-005 escalations (2026-10-09)

Ownership: `harness.py`, `orchestrate.py`, `telemetry.py` and the SDD-OBS-001 DAG are Showcase-owned (ADR 0001).
Harness does not author or authorize these tasks. The owner authorizes through `human-resolve`; the lifecycle carries
it out. State (read-only): T-004 escalated (attempt 1, last commit `890b48b`); T-005 escalated (attempt 1, last commit
`946b663`); T-006 waits for both, so the DAG reports `NO_READY_TASKS`.

The existing commands cover this; no new task or feature replan is needed.

1. T-004 already owns the files: its `allowed_paths` include `harness.py`, `orchestrate.py` and their tests. The three
   open fixes are inside that scope:
   - fail closed on the malformed gate that crashes;
   - make repository admission and claim one CAS under the same lock, which removes the ownership TOCTOU;
   - add a durable same-family resume path for verification blockages.

   Add the lifecycle CAS API T-005 needs (trusted history metrics and manual-review registration) to T-004's
   objective. Path: `human-resolve` (escalated → failed) with that decision, then `replan-task --expected-status failed`
   with a proposed T-004 revision stating the four items (criteria must already exist in the spec or verification
   contract), then `authorize-retry`, then `start`; the retry continues from `890b48b`.
2. T-005 consumes that API instead of reaching into the lifecycle. Path: `human-resolve`, then `replan-task
   --expected-status failed` with a revision whose `depends_on` adds `T-004` (task replan accepts dependency changes;
   no cycle, since T-006 already depends on both), with `allowed_paths` still limited to `telemetry.py` and its tests,
   then `authorize-retry` once T-004 completes; the retry continues from `946b663`.
3. Keep the four untracked root evidence files out of these tasks; commit them in a separate docs change.

Harness reviews the T-004 checkpoint (API shape, lock scope, malformed-gate tests) before T-005 starts on it.

## 8. Second replan of SDD-OBS-001 T-004 (2026-10-09)

State (read-only): T-004 escalated, attempt 4, active revision `sha256:f975478f…100f` (first replan, committed
2026-10-09T11:30Z); malformed-gate fix preserved in `2cb6162`. Its scope is still `harness.py`, `orchestrate.py` and
their tests, but VC-037/038/039 need verifier-side admission/reservation changes in `verification/*` (T-001/T-002
files). T-005 failed, revision `sha256:98697cc9…de1`, depends on T-004.

Cause (`harness.py` at `78ea4dd`, `cmd_replan_task`): `if prior_requests: … die('REPLAN_CONCURRENT_CONFLICT: a
different replan request is already recorded')`. Any recorded replan blocks every later one, so a task can be
replanned once in its lifetime. The rest of the ledger already supports chains: `resolve_active_packet` checks
`len(replan_requests) == len(packet_lineage) - 1` and a connected lineage.

Recommended: a separate Showcase protocol feature (as `WORKTREE-PROTOCOL-REFRESH-001` was) that allows successive
replans:
1. A new request is accepted only when `expected_active_revision` equals the active revision and the lineage's last
   request produced that revision; the concurrency guard stays (a racing replan built on the same active revision
   loses the CAS).
2. A failed-task replan needs a human resolution newer than the last committed replan.
3. Tests (red first): a second replan after a fresh human resolution succeeds and extends the lineage; a stale
   expected revision, a reused human resolution and two concurrent second replans are refused; `resolve_active_packet`
   accepts the three-entry lineage; idempotent replay still prints `ALREADY_REPLANNED`.

Then T-004: `human-resolve`, then `replan-task --expected-status failed --expected-active-revision sha256:f975478f…` with a
revision adding the verifier admission/reservation files, then `authorize-retry`, then `start` from `2cb6162`.

Alternative without a protocol change: `reopen` T-002 (owner of `verification/store.py`, `executor.py`,
`supervisor.py`) to add verifier-side admission there. That invalidates T-003, T-004 and T-005 and still needs a T-002
contract change, so it costs more than the protocol fix.
