# Harness review of Showcase SDD-OBS-001 T-009 replanned packet (2026-10-09)

- Scope: read-only review of the active T-009 revision `sha256:ed77124f…e6df20` (state: failed, attempt 1, no
  implementation) against the original `tasks.json` entry and the SDD-OBS-001 spec/plan.
- Verdict: **accept with two amendments** in one more replan before implementation.

## What the revision adds (and correctly keeps)

- Criteria: AC-OBS-060, AC-OBS-058 and AC-OBS-048 added; all 35 original criteria kept.
- Scope: `telemetry.py` and `tests/test_telemetry.py` added (needed to extend `verify_manual_observation_for_coverage`
  to attestations that carry a plan and obligations); `test_telemetry.py` added to verification.
- Objective: coverage is bound in the same `.agent-state` transaction to the exact accepted plan, obligation, candidate
  and surface, with the verifier called inside it; every existing fail-closed admission, consumption, receipt and
  backend-qualification requirement is kept.

## Amendments

1. `test_seam` names no coverage case, so red-first coverage tests are not required by the packet. Add plan items
   50/51/53 (MANUAL-M1–M10, MANUAL-AUTH-1–6) and these lifecycle cases:
   - coverage refused for a superseded or historical plan, a wrong obligation, and a dirty or non-exact candidate/surface
     (`MANUAL_EVIDENCE_CANDIDATE_BINDING_REQUIRED`, exit 5, no mutation);
   - stored attestation or report bytes changed between registration and coverage → refused inside the CAS;
   - exact replay is idempotent and a conflicting replay is refused;
   - coverage racing a replan or completion on the same lifecycle lock (one wins, the other observes it);
   - manual evidence never acts as retry authorization and a grant never counts as criterion evidence (AC-OBS-048).
2. `allowed_paths` lacks `verification/admission.py` and `tests/test_verification_admission.py`. T-004 put the
   repository admission there. If committing admission plus `launch_reservation` touches it, T-009 would need a third
   replan. Add both now (no cost if unused).

## Notes

- Size: with coverage added, T-009 carries 38 criteria across lifecycle, executor, supervisor, store, runner,
  orchestrator and telemetry. Keep the reviewers' checkpoint commits small enough to review per area.
- Verification commands call `python3`; on the local Mac that is 3.9.6, so T-005 needed a shim. Keep the
  completion evidence explicit about the interpreter, or use Linux CI's Python 3.13 as the normative run.

Path: `human-resolve`, then `replan-task --expected-status failed --expected-active-revision sha256:ed77124f…` with the
two amendments, then `authorize-retry`, then `start`. Harness reviews the implementation before completion.
