# Harness review of Showcase SDD-OBS-001 T-005 attempt 2 (2026-10-09)

- Scope: read-only review of the uncommitted T-005 worktree diff (`telemetry.py`, `tests/test_telemetry.py`) on
  `f052609` (T-004 checkpoint `0019649` merged); experiments ran in a clone with the identical diff applied.
- Verdict: **implementation sound, tests insufficient.** T-005 completes after the negative tests below. Manual
  coverage acceptance is unowned in the DAG (see last section).

## Evidence

- `test_telemetry.py`: 13 OK (`cryptography` 46.x).
- Flow: `--attestation` is required. Scope is resolved under the lifecycle lock; the JCS + detached Ed25519
  attestation is verified against the committed issuer registry, and the signed envelope is compared with the
  expected scope. Snapshots are stored by digest; then `register_manual_observation` re-resolves scope under its own
  lock with a generation CAS. `--plan-id` is refused (`MANUAL_EVIDENCE_PLAN_BINDING_UNAVAILABLE`), so nothing
  registers as coverage.
- Mutations against the 13 tests:

  | Mutation | Result |
  |---|---|
  | Ed25519 verification removed | caught |
  | `--attestation` made optional | caught |
  | registry bytes not required to equal committed `HEAD` | **survives** |
  | revoked issuer accepted | **survives** |
  | signed envelope not compared with expected scope (feature/task/attempt/report/checkpoint) | **survives** |
  | issuer without `manual-review` action accepted | **survives** |
  | non-canonical attestation bytes accepted | **survives** |
  | stored attestation digest not re-checked in `verify_manual_observation_for_coverage` | survives (caught later by the signature step) |

## Conditions (before completion)

1. One negative test per surviving check:
   - registry modified but not committed;
   - revoked issuer;
   - issuer lacking `manual-review`;
   - non-canonical (re-serialized) attestation bytes;
   - a validly signed attestation for a different feature, task, attempt, report digest or checkpoint, one case per
     field.

   Each case must be refused before `register_manual_observation` is called.
2. Low: `payload.startswith(b'\\xef\\xbb\\xbf') or payload.endswith(b'\\n')` compares literal backslash sequences, not a
   BOM or newline. The JCS equality check catches both anyway; fix the literals or drop the line.

## Coverage acceptance is unowned

AC-OBS-060 (manual coverage only through `.agent-state` CAS with an exact plan/candidate/surface binding), AC-OBS-058 and
AC-OBS-048 appear only in T-900 and T-990, the evaluation and gate tasks. No builder task implements them. T-005 should
not: its scope is `telemetry.py`, and its objective keeps manual observations from authorizing transitions. The CAS
belongs in the lifecycle (`harness.py`/`verification/authority.py`), which T-009 owns, and T-009 runs after T-005 via
T-006.

`verify_manual_observation_for_coverage` today verifies only telemetry-only attestations (`plan_id` null,
`obligation_ids` empty); the coverage path must verify coverage-bearing envelopes and call it inside the coverage CAS.
