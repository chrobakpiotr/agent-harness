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
