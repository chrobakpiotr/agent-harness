# Showcase SDD-OBS-001 T-009 review (checkpoint `1babc6c`, 2026-10-13)

- Scope: Harness review of T-009 attempt 1 at local commit `1babc6c`, which has parent `8cdc9b5` (T-006). The review
  is a read of the diff plus independent runs on Linux (`python:3.13.16-slim`, cryptography 49.0.0) against an
  export of the commit.
- Verdict: **changes required (R1–R4).** The mechanism is sound. The verification command as declared does not
  pass, and key guards that the test seam names are not tested.

## Verified

- **Scope.** 10 files, all within T-009's `allowed_paths`. The worktree is clean at `1babc6c`.
- **Admission and reservation in one CAS.** `admit_verification_execution` runs under `lifecycle_state_lock`. It
  checks generation, plan and accepted authority, requires a `running` task on the plan's attempt, and refuses a
  unit already `launch_reserved`/`launch_consumed`/`execution_started` (`VERIFICATION_EXECUTION_IN_FLIGHT`). It then
  publishes the immutable admission record and records admission and reservation in one `.agent-state` write.
- **Consumption.** `consume_verification_launch` spends only a `launch_reserved` reservation that matches the
  reservation ID and admission hash. It refuses a second spend, re-checks the attempt, publishes the consumption
  record and returns a process-local capability.
- **The replan barrier is central.** `lifecycle_state_lock` refuses `verification-owned` while any reservation is
  live (`launch_reserved`, `launch_consumed`, `execution_started`), except on that reservation's own
  `allowed_reservation` path.
  - `replan-task`, `reconcile-feature` and `reopen` all go through it.
  - The remaining raw-lock writers are recovery operations that need `.agent-state` absent, immutable legacy and
    packet-history publication, and reads. `heartbeat` skips admission only to renew its lease.
- **Tests rerun by Harness:**

  | Packet command | Result |
  |---|---|
  | `test_verification_authority.py` | 23 OK |
  | `test_verification_completion_boundary.py` | **fails, standalone (R1)** |
  | executor, supervisor, store, lifecycle | 125 OK |
  | `test_harness.py`, `test_runner.py`, `test_orchestrate.py` | 308 OK (sharded, 6 workers, 140 s) + runner and orchestrate OK |
  | `verification_contract.py validate` | OK |

  With the path provided, the boundary module passes 6 OK. The Git ref-lock line in the sharded run is known
  fixture noise.

## Required

- **R1. Packet command 2 fails as declared.** `python3 -m unittest
  tooling/agent-harness/tests/test_verification_completion_boundary.py -v` raises `ModuleNotFoundError: No module
  named 'harness'` in a clean checkout, because the module imports `harness` before `test_harness` has put the
  harness directory on `sys.path`.
  - The defect predates T-009 (base `8cdc9b5` fails identically). CI's `discover` hides it by importing
    `test_harness` first.
  - T-009 owns the file and declares the command, so it must pass standalone. Insert the harness and tests
    directories on `sys.path` before `import harness`, or import `test_harness` first.
  - The CI fixture commit `e43a170` on `main` changes the same module (it adds the seventh, stale-checkpoint test).
    Keep both changes at integration.
- **R2. The replan barrier is untested.** With the `verification-owned` refusal for live reservations disabled in
  `lifecycle_state_lock`, every test still passes: the lifecycle, authority and supervisor modules (113), all of
  `test_harness` (308) and runner/orchestrate/boundary (49). The objective ("preserve the reservation as a replan
  barrier") and the seam ("replan-vs-admission CAS races") both require it.
  - Add tests on the real `.agent-state` fixture showing that `replan-task`, `reconcile-feature` and `reopen` are
    refused with `verification-owned` in each live state: `launch_reserved`, `launch_consumed`, `execution_started`.
  - Also test that admission refuses a plan made stale by a replan that won the race.
- **R3. Duplicate consumption is untested.** With both the `VERIFICATION_LAUNCH_ALREADY_CONSUMED` and the
  `launch_reserved`-status refusals disabled in `consume_verification_launch`, all 148 core tests and all 308
  `test_harness` tests still pass. Disabling either one alone is not detected either. The seam names "duplicate
  reservation consumption".
  - Add a test that a second consume of the same reservation (sequential, and after a lost reply) is refused, and
    that it issues no second capability.
- **R4. Guard coverage evidence.** No test references `VERIFICATION_LAUNCH_ALREADY_CONSUMED`,
  `VERIFICATION_LAUNCH_RESERVATION_STALE`, `VERIFICATION_ATTEMPT_BINDING_MISMATCH` or
  `VERIFICATION_EXECUTION_IN_FLIGHT`. Disabling the attempt-binding check at consumption is also undetected; that one
  may be redundant with the barrier, so say so if so.
  - For each seam item, give the guard that enforces it and a test that fails when only that guard is disabled:
    task-only-to-independent upgrade, missing/extra/stale obligation membership, replan-vs-admission race,
    duplicate consumption, crash or lost reply after each durable transition, stale executor or capability,
    mismatched terminal receipt, and absent qualified backend.
  - Assert refusal codes, not a generic `StoreError`.
  - Record the mutation results in the completion evidence. Harness checked R2 and R3 itself and will spot-check the
    rest.
