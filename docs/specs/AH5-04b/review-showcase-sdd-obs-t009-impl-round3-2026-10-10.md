# Harness review round 3 of Showcase SDD-OBS-001 T-009, branch `agent/SDD-OBS-001/T-009` at `6857169` (2026-10-10)

- Scope: read-only; clones at `6857169`; active packet `sha256:4bf89ee9…` (revision of `c8badc31` that only extends
  `test_seam` with the round-2 requirements; accepted). Follows
  [`review-showcase-sdd-obs-t009-impl-round2-2026-10-10.md`](review-showcase-sdd-obs-t009-impl-round2-2026-10-10.md).
- Verdict: **pass with conditions.** Every behavioural finding is fixed. Remaining conditions are mutation-sensitive
  tests the packet's `test_seam` requires; fix them in the same attempt, then complete through the lifecycle and run
  the plan's independent evaluation (T-900).

## Suites (Python 3.13, `cryptography` 46), all OK

`test_harness` + `test_runner` + `test_orchestrate` 356; authority 18; completion boundary 6; executor + supervisor +
store + lifecycle + admission 140; telemetry 29; profile + Showcase profile + parity 29; `verification_contract.py
validate` OK. Scope: every changed file is inside `allowed_paths` (`machine_outcomes.py` change removed).

## Round-2 findings

| Finding | Status | Evidence |
|---|---|---|
| `test_telemetry` failures | fixed | 29 OK |
| `machine_outcomes.py` scope | fixed | no out-of-scope file |
| plans validated without repository | fixed | `0348645` |
| end-to-end coverage | fixed | `test_real_plan_registration_and_coverage_use_committed_profile_and_signature`: real git repo, committed profile and registry, real signature, plan, registration and coverage, no mocks on those boundaries |
| NEW-1 evidence breaks sealing | fixed | `record_manual` stores under `VerificationStore(primary).root` (`telemetry.py:831`) |
| prelaunch stranding | fixed | fork probes: consume then kill, consume then exception, and reserve then exception all end `safe_prelaunch_abort` for reservation and consumption, the replan gate passes, nothing launched; retrying the same plan is refused (`EXECUTION_LAUNCH_AUTHORITY_RECOVERY_REQUIRED`, replan required) |
| executor wiring | fixed | real `execute_plan` probe drives reservations to terminal |
| reason codes | fixed | unknown plan, dirty candidate and unknown observation → `MANUAL_EVIDENCE_CANDIDATE_BINDING_REQUIRED`; coverage command added (`848709b`) |
| profile guards (round-1 #11) | fixed | principal on a non-manual gate and a profile file without origin fields are now caught |
| coverage guards (#9) | fixed except low | all caught except the feature-fingerprint check (low) |

## Conditions (tests only; mutations survive)

1. Coverage lock: replacing the coverage `lifecycle_state_lock` with `nullcontext` passes all 22 coverage, race and
   serialization tests (two runs). Assert the outcome of a concurrent completion/replan, as in T-004 round 2.
2. Checkpoint proof: removing the seal comparison (`seal.head_sha != reviewed_checkpoint …`) or the commit-type check
   survives the coverage tests, including the real-plan test. Add a real-plan case with a changed reviewed checkpoint
   and one with a non-commit object.
3. Abort guards: the `harness_invocation_upper_bound == 0` check in `harness.py` survives; the other round-1 #10 guards
   are being rerun and are appended below.
4. Low: the feature-fingerprint check in coverage.

Also include the benchmark (it passes alone; it failed only under parallel CPU load) in the completion evidence.

## Abort-guard mutations (completed run)

Caught: the `store.py` safe-abort shape and upper bound, `supervisor.py` authority-state → `UNCERTAIN`, `harness.py`
replay accepting any receipt hash, executor authorizer set to `None`, and the recovery rebuild disabled. Survives: only
the `harness_invocation_upper_bound == 0` check in `harness.py` (condition 3 stands for that one guard).
