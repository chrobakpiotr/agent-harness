# Harness review of Showcase SDD-OBS-001 T-009 attempt 5 (2026-10-10)

- Scope: read-only review of the T-009 worktree (commit `6c9f492` plus the uncommitted diff, 13 files) against packet
  `sha256:53e19458…6296`. Experiments ran in a clone with the identical state; an independent evaluator probed coverage
  and prelaunch abort, and I confirmed its high findings.
- Verdict: **fail; do not complete.** The guards that exist fail closed, so nothing launches or gains coverage
  wrongly. But manual coverage cannot succeed end to end, the AC-OBS-060 checkpoint proof is missing, a prelaunch
  crash strands lifecycle state, and the registered verification cannot pass.

## Evidence

- Suites (Python 3.13, `cryptography` 46):
  - `test_harness` + `test_runner` + `test_orchestrate`: 353 OK;
  - `test_verification_authority`: 17 OK;
  - `test_verification_completion_boundary`: 6 OK (it needs `PYTHONPATH=tooling/agent-harness:…/tests`, unchanged since
    T-006);
  - executor + supervisor + store + admission: 124 tests, one load error;
  - `test_telemetry`: 25 OK;
  - profile + Showcase profile + parity + benchmark: 26 OK;
  - `verification_contract.py validate`: OK.
- Profile mutations (39 tests): ambiguous, disabled/revoked or non-`manual-review` principal, an unchecked manual gate,
  the principal missing from the obligation id, and a task occurrence upgraded to independent are all caught.

## Blocking findings

| # | Severity | Finding |
|---|---|---|
| 1 | high | Registered verification fails as written. The third command names `tests/test_verification_lifecycle.py`, which does not exist (`ModuleNotFoundError`). It is in `allowed_paths`; create it, with the real-`.agent-state` lifecycle coverage and abort tests the seam asks for. |
| 2 | high | Manual coverage can never succeed in production. `record_manual` verifies against an envelope with `plan_id`, `family_id`, candidate, surface and generation `None` and `obligation_ids` empty, and refuses `--plan-id` (`telemetry.py:693`, 744–748). `verify_manual_observation_for_coverage` requires those signed fields to equal the plan binding (`telemetry.py:503–513`), and `record_manual` is the only writer of stored attestations. Every harness coverage test mocks the verifier, `validate_plan_record` and `_load_trusted_profile`. Needed: plan-bound registration, plus one unmocked registration → coverage test. |
| 3 | high | AC-OBS-060 checkpoint proof missing. Coverage never resolves the report's reviewed checkpoint, never recomputes its manifest and surface against the plan's `base_sha`, and creates no immutable checkpoint-binding record. It trusts the signer's candidate/surface (`harness.py:2414–2435`). The spec forbids substituting a caller assertion for that proof. |
| 4 | high | Prelaunch crash strands the attempt. The supervisor releases the runtime lock, reserves and consumes, calls `reacquire(timeout=0)`, then publishes `launch-authority.json` (`supervisor.py` ~609–650). Reproduced: a failure after the consume CAS leaves `launch_consumed` with no authority record; `recover()` returns `UNCERTAIN / EXECUTION_LAUNCH_AUTHORITY_ABSENT` on every retry; a new consume gives `VERIFICATION_LAUNCH_ALREADY_CONSUMED`; replan is refused (`VERIFICATION_RESERVATION_ACTIVE`, `harness.py:4226`). `safe_prelaunch_abort` is never written to state, and there is no abort for a reserved-only reservation. A crash after the reserve alone does recover. |
| 5 | medium-high | The executor never wires the launch authorizer: `launch_authorizer = None` (`executor.py:169`); `authorize_launch` is defined (~200) but never assigned, so every plan-bound `execute_plan` fails `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE`. Tests pass their own authorizer to the supervisor. |

## Other findings

| # | Severity | Finding |
|---|---|---|
| 6 | medium | Wrong codes: a dirty candidate gives `invalid-verification-plan` and an unknown plan gives `ACCEPTED_PLAN_UNAVAILABLE`; the spec requires `MANUAL_EVIDENCE_CANDIDATE_BINDING_REQUIRED` with exit 5. `load_plan_record` and `validate_plan_record` run before the lock, outside the error mapping. There is no coverage CLI, so exit 5 is unreachable. |
| 7 | medium | The candidate reseal (`validate_plan_record(reconstruct=True)`) runs before `lifecycle_state_lock`, so a candidate that turns dirty before the CAS is not caught; `admit_verification_execution` reseals inside the lock (`harness.py:1103`). |
| 8 | medium | Concurrency test can't fail: with the coverage lock replaced by `nullcontext`, `test_manual_coverage_serializes_with_completion_on_lifecycle_lock` passed three of three runs (0.05 s timing). No replan race test. Assert the outcome, as in T-004 round 2. |
| 9 | medium | Untested coverage guards (mutation survives): the harness check that the proof principal equals the profile principal, and the telemetry check that the envelope principal equals the binding principal (removing both is undetected); feature fingerprint; scope attempt; ledger `record_sha256` binding (a swapped record goes unnoticed); ledger uniqueness; `expected_feature_generation` CAS. A probe confirms that a validly signed attestation from another principal is refused today. |
| 10 | medium | Untested abort guards: `store.py:120` safe-abort shape check, the `harness_invocation_upper_bound == 0` condition, `supervisor.py:860` authority-state → `UNCERTAIN`, and `harness.py:1287` accepting any receipt hash on terminal replay. |
| 11 | medium | Untested profile guards: a principal on a non-manual gate, an independent gate without class or required sandbox, and a persisted profile file without explicit origin fields are all accepted with tests green. Removing the principal from the family id also survives (low: `profile_hash` still changes the plan id). |
| 12 | low | AC-OBS-048 is met only vacuously. Nothing reads `manual_coverage_ledger` (neither completion nor grants), and the grant test passes because the ledger is absent. |

## Required before completion

1. Create `tests/test_verification_lifecycle.py` so the registered verification runs.
2. Plan-bound manual registration (attestation signed with the plan binding), plus one unmocked registration → coverage
   test.
3. Trusted checkpoint → candidate/surface proof and an immutable checkpoint-binding record inside the coverage CAS,
   with the reseal also inside the lock.
4. Closing the prelaunch stranding window:
   - an abort CAS for reserved or consumed reservations that were never launched, recorded as `safe_prelaunch_abort`;
   - recovery that terminalizes a consumed reservation that has no authority record and was never launched;
   - tests that kill the process at each point between reserve and the launch marker.
5. Wiring `authorize_launch` into the executor, with a test through `execute_plan`.
6. The spec reason code (exit 5) for coverage refusals.
7. Tests that pin findings 8–11.

Prior conditions still apply: completion evidence includes parity and benchmark (26 OK here).
