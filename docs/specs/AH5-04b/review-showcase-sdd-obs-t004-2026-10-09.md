# Harness review of Showcase SDD-OBS-001 T-004 attempt 6 (2026-10-09)

- Scope: read-only review of the uncommitted T-004 worktree diff on `a35b5a8`, active revision
  `sha256:ed5f0b44…1584`; all 10 changed/new files are inside its `allowed_paths`. Experiments ran in a clone with
  the same diff applied.
- Verdict: **not ready to complete.** Two blockers: a stranded admission freezes the repository, and
  `register_manual_observation` is missing although the objective names it. Malformed-gate handling and resume
  evidence pass.

## Evidence

- Suites: `test_harness` + `test_orchestrate` 303 OK; `test_verification_supervisor` 74 OK (it needs `cryptography`; on
  Intel macOS only `<47` has a wheel); `test_verification_store` 28 OK; `test_verification_admission` 5 OK.
- Malformed gates: 60,000 randomly mutated verifier results (wrong types, missing/extra keys, NaN/inf, huge ints,
  bytes, nested junk) through `verification_evidence_summary`: no exception; malformed shapes map to
  `verification-blocked` / `MALFORMED_VERIFICATION_RESULT`.

## Findings

| # | Severity | Finding |
|---|---|---|
| 1 | high (blocker) | Stranded verification admission. `lifecycle_admitted` releases the reservation on an exception before `launching.json`, but a hard kill (SIGKILL, OOM, power loss) between `reserve_verification` and the launch marker leaves it active with no start journal. `VerificationSupervisor.recover()` releases only executions with `drained.json`, so it leaves this one; no CLI calls `recover()` at all. Reproduced: reservation with a dead `owner_pid` and no journal → `recover()` returns `()`, reservation still active, `RepositoryAdmission.mutation()` raises `verification-owned`. Every lifecycle mutation in the repository (`start`, `complete`, `fail`, `release`, `recover-stale`, `replan-task`, `human-resolve`) is then refused, with no supported way out but editing `.agent-state`. |
| 2 | high (blocker) | The active objective requires "trusted lifecycle APIs for … manual-review registration used by T-005"; the diff provides `trusted_task_history` and `resolve_manual_review_scope` only, no registration. |
| 3 | medium | Resume binds plan, attempt, generation and reason digest, but not the prior builder result. `run_resumed_verification` re-reads `last_failure_evidence` (any file under `.agent-runs/<feature>` with `status: pass`) and copies it into the resume result; its bytes can change between `resume-verification` and the run. Record its sha256 in the resume tuple and check it before use. The candidate itself is bound: the verifier compares the worktree's candidate seal with the accepted plan. |
| 4 | low | `resume-verification` records no operator (`--by`), unlike `human-resolve`/`replan-task`; the resume `start` branch skips `is_unrecovered_partial_claim`. |
| 5 | low | `repository_id` differs between APIs: `trusted_task_history` returns the Git common-dir path, while admission uses its sha256. Pick one for T-005. |

Lock scope (ok): `lifecycle_state_lock` takes the repository admission lock, then the feature lock. The verifier
reserves under the admission lock only, with plan validation inside it. `assert_repository_verification_drained` takes
the store's repository lock and releases it before the lifecycle lock. `heartbeat` deliberately skips admission. No
nested reverse order found. While a verification is active, every lifecycle mutation in the repository is
serialized behind it; that is the accepted design.

## Required before completion

1. Stranded admission: `recover()` must release a verification reservation that has no start journal once its owner is
   proven gone (pid plus process start time, not pid alone, because pids are reused), and recovery needs a CLI (for
   example `harness.py recover-verification`) that works while mutations are blocked. Test: SIGKILL a child after
   `reserve_verification`, then the CLI, then `start` succeeds.
2. `register_manual_observation(repository, feature_id, *, observation_id, role, task_id=None, task_attempt=None,
   checkpoint=None, attestation_sha256, report_sha256, expected_feature_generation)`:
   - runs under `lifecycle_state_lock`;
   - re-resolves scope inside the lock through `resolve_manual_review_scope`, never from a caller-supplied scope;
   - checks the feature generation as CAS;
   - publishes one immutable record and a state ledger entry;
   - is idempotent by `observation_id` (same bytes return the existing record; different bytes are refused);
   - takes no metric values.

   Who verifies the signed attestation (this API or T-005's telemetry) must be stated. Tests cover stale generation,
   wrong role, unresolved attempt, replay, conflicting replay, and a concurrent mutation.
3. Bind the prior builder result digest into the resume tuple (finding 3).

## Integration note

The base `a35b5a8` is the `SEQUENTIAL-TASK-REPLAN-001` checkpoint, stacked on the T-004 failed-attempt commits; it is
not on `main` (`78ea4dd`). Integrate that protocol change to `main` on its own so it does not ride in with T-004's
checkpoint.

T-005 stays unstarted until items 1–2 land and Harness reviews the registration interface.
