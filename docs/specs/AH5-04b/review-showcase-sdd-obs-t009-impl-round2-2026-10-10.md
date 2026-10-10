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
