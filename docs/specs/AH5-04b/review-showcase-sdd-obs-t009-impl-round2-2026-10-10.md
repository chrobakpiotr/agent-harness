# Harness review round 2 of Showcase SDD-OBS-001 T-009 attempt 5, commit `2e45a229` (2026-10-10)

- Scope: read-only; clone at `2e45a229`; follows
  [`review-showcase-sdd-obs-t009-impl-2026-10-10.md`](review-showcase-sdd-obs-t009-impl-2026-10-10.md).
- Verdict: **fail; do not complete.** Two blockers that the lifecycle itself would refuse; the coverage/abort
  re-check by an independent evaluator is appended below when it finishes.

## Suites (Python 3.13, `cryptography` 46, `PYTHONPATH=tooling/agent-harness:…/tests`)

| Suite | Result |
|---|---|
| `test_harness` + `test_runner` + `test_orchestrate` | 355 OK |
| `test_verification_authority` | 18 OK |
| `test_verification_completion_boundary` | 6 OK |
| executor + supervisor + store + lifecycle + admission | 127 OK (`test_verification_lifecycle.py` now exists) |
| profile + Showcase profile + parity | OK; benchmark OK when run alone (38 s; it failed only under parallel CPU load) |
| `test_telemetry` | **27 run, 2 FAIL** |
| `verification_contract.py validate` | OK |

## Blockers

1. `test_telemetry` fails:
   `test_manual_attestation_rejects_valid_signature_for_different_task_before_register` and `…_different_attempt_…` now
   raise `MANUAL_EVIDENCE_REPORT_INVALID` where the tests expect `MANUAL_EVIDENCE_ATTESTATION_INVALID`. The input is
   still refused, but this is a registered verification command. Either restore the order (attestation scope check
   first) or update the expectation deliberately, keeping the "refused before `register_manual_observation`"
   assertion.
2. Out of scope: `tooling/agent-harness/machine_outcomes.py` (adds `MANUAL_EVIDENCE_CANDIDATE_BINDING_REQUIRED` to
   `BLOCKED_REASONS`) is not in T-009's `allowed_paths`; the completion `allowed_paths` check will refuse the checkpoint.
   Move the mapping into an allowed file or replan T-009 to add `machine_outcomes.py` and its test.

## Still open from round 1 (finding 11)

Mutations still survive: a principal on a non-manual gate is accepted (`profile.py:220`), and a persisted profile file
without explicit origin fields is accepted (`profile.py:172`). An independent gate without class or required sandbox is
now caught.

## Broader suite

The reported stale `constitution_sha256` inputs and the completion-correction concurrency failures are outside the
registered verification; compare them against `main` before attributing them to T-009.

## Coverage and abort re-check (independent evaluator; high findings confirmed by Harness)

| Round-1 # | Status | Evidence |
|---|---|---|
| 2 end-to-end coverage | **not fixed** | `record_manual` now signs a plan-bound envelope, but the only "unmocked" test still mocks `load_plan_record`, `validate_plan_record`, `_load_trusted_profile` and `seal_candidate`. A real run fails: `store.py:612` and `:628` call `validate_plan_record(record)` without `repository`, so a profile with a manual gate raises `invalid-manual-reviewer-registry`, and no plan with a manual obligation can be published or loaded (confirmed). With that and NEW-1 worked around in a scratch copy, register → coverage succeeds, so the logic is right but unreachable. |
| 3 checkpoint proof | logic yes, tests no | Trusted `seal_candidate`, commit-type check and an immutable binding record exist (`harness.py:2551–2586`). Removing the seal comparison or the commit check survives, because every test stubs `seal_candidate`. |
| 4 prelaunch stranding | **partial** | A hard kill after consume now recovers to `safe_prelaunch_abort` and replan passes. Still stranded forever (`UNCERTAIN / EXECUTION_LAUNCH_AUTHORITY_ABSENT`, replan refused, every later execute `verification-owned`): (a) consume then an exception, such as a failed `reacquire(timeout=0)`, which releases the admission so owner death can't be proven; (b) reserve only, killed or exception, with no reserved-only abort CAS. (b) regressed: it recovered in round 1. |
| 5 executor wiring | fixed | `executor.py:231`; a real `execute_plan` with a fake backend drives three reservations to `execution_terminal`. |
| 6 reason code / exit 5 | partial | The API returns `MANUAL_EVIDENCE_CANDIDATE_BINDING_REQUIRED`. Through `record-manual --plan-id` the exit is 5 but the codes are wrong: an unknown plan gives `MANUAL_EVIDENCE_SCOPE_UNAVAILABLE`, a dirty candidate `MANUAL_EVIDENCE_CHECKPOINT_UNAVAILABLE`. The exit-5 test mocks `record_manual`; there is still no coverage CLI. |
| 7 reseal in lock | code yes, tests no | `harness.py:2471`, `telemetry.py:747`; deleting either survives every test. |
| 8 concurrency test | **not fixed** | With the coverage lock replaced by `nullcontext` the test passes 3/3 (0.05 s timing); no replan race test. |
| 9 coverage guards | mostly fixed | All are now caught except the feature-fingerprint check (`harness.py:2484`, low; the authority-binding equality likely covers it). |
| 10 abort guards | **not fixed** | Still survive: the `store.py:120` safe-abort shape, `harness_invocation_upper_bound == 0` (`store.py:116`, `harness.py:1347`), `supervisor.py:888` authority-state → `UNCERTAIN`, and `harness.py:1342` replay accepting any receipt hash. |
| 12 AC-OBS-048 | not fixed (low) | Nothing reads `manual_coverage_ledger`. |

New:

- **NEW-1 (high, confirmed):** `record_manual` stores attestations and report snapshots under
  `.agent-runs/manual-attestations/` and `.agent-runs/manual-report-snapshots/`, outside the runtime root that
  `seal_candidate` excludes (`candidate.py:122–133`). After one `record-manual`, sealing reports
  `SECRET_BEARING_CANDIDATE_UNSEALABLE` and `admit_verification_execution` fails `invalid-verification-plan`, which
  blocks every plan-bound verification, coverage and registration in the repository. Store them under the excluded
  control root.
- **NEW-2 (medium):** the telemetry pre-check allows role `reviewer` on a builder task (`telemetry.py:701`); trusted
  `resolve_manual_review_scope` then rejects it with `MANUAL_EVIDENCE_TASK_ROLE_MISMATCH`. Align the two.

## Required before completion (cumulative)

1. The two `test_telemetry` failures; `machine_outcomes.py` scope.
2. `validate_plan_record(…, repository=…)` in `store.py` publish/load; manual evidence stored under the excluded control
   root (NEW-1); one truly unmocked registration → coverage test (real plan, profile, seal, signature).
3. Recovery that terminalizes consume-then-exception and reserve-only (kill and exception) to `safe_prelaunch_abort`,
   with fork/kill tests for each.
4. Spec reason codes through `record-manual --plan-id`; a coverage entry point that returns exit 5.
5. Tests that fail when removed: the seal comparison and commit check, both reseals, the coverage lock (assert the
   outcome), a replan race, the round-1 #10 abort guards, and the two profile guards (finding 11).
6. NEW-2.
