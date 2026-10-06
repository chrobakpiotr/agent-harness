# AH5-05a — Offline launch/cancel API (fake and controlled backends)

- Status: **accepted** and **done** (re-evaluated 2026-10-06: pass); agent-benchmark review: accepted once
  `0.2.0` is tagged (see ADR 0004, "Consumer review").
- Source: master plan AH5-05 ("03b for the offline API; 04c for hardened live"); ADR 0002 "Not decided here"
  (launch/cancel API). Consumer: agent-benchmark AB5-06b (`docs/execution-port.md`, open item 3; AB5-06a
  test "cancellation tied to real terminal/drain" is NOT RUN until this exists). Independent of the Showcase
  design gate and of 04a-2/04b.

## Findings (2026-10-06)

- Contract v1 already carries everything a launch returns (result, attempts, drain, cancel, candidate
  reference, usage). Benchmark builds v1 requests and imports v1 results through one adapter
  (`harness_port.py`) and runs its own scripted fake in place of a backend.
- `CapabilityReport` chains `launch_ready ⇒ qualified`, so a fake/controlled backend can never report
  launch-ready, although v1 lets it answer requests without `qualified_isolation` (`isolation_level:
  fake/controlled`).
- S30-06 (Showcase `c9a5fec`, `a6fac3c`): REF-Q/PROD-Q map to separate capability-report targets; no
  contract change. Recorded dependency only.

## Proposed scope

Public module `agent_harness.execution` (ADR 0004), contract v1 unchanged:

- `launch(request, repo, backend) -> Execution`: validates the request and `RepoContext`; refuses without
  side effects (`rejected` + `CAPABILITY_UNSUPPORTED`/`NOT_QUALIFIED`) when the backend lacks a required
  capability; otherwise starts the backend.
- `Execution.cancel()` records `cancel_requested`; `Execution.result(timeout=None)` returns the terminal result,
  validated with `contract.validate_result` (an invalid backend result becomes `unknown`, never success).
- Idempotency: the result is published create-once under `evidence_root/executions/<request_id>.json`;
  launching the same `request_id` again returns it (crash reconcile), a different request with that ID is
  `BINDING_MISMATCH`.
- `ScriptedBackend(steps)`: deterministic per-attempt outcomes, usage and candidate bytes;
  `isolation_level: fake`. Replaces the benchmark's own fake.
- `ProcessBackend(argv)`: a real local child process in `repo.workspace`, own process group; timeout and
  cancel terminate the group, `drain: confirmed` only when the group is gone; candidate = a declared output
  file sealed by digest; usage `unknown`. `isolation_level: controlled` (no sandbox, never qualified).
  POSIX only.
- Capability reports for both backends (see decision 2).

Out of scope: real providers, sandboxes, qualified isolation (04b/04c), retries beyond the request's
`max_attempts` script, Showcase changes.

## Acceptance criteria (draft)

- AC1: every result returned by `launch` validates against its request; fixtures for completed, error,
  timeout, cancel, unknown and rejected.
- AC2: cancel of a running `ProcessBackend` child (and its grandchild) yields `outcome: cancel`,
  `cancel_requested: true`, and `drain: confirmed` only after the whole process group has exited; a
  surviving descendant yields `drain: unconfirmed`.
- AC3: a required capability the backend lacks gives `rejected` with no process started and nothing written.
- AC4: relaunching a `request_id` returns the stored result; a changed request with the same ID is refused.
- AC5: the minimal consumer and a benchmark-shaped test use only `agent_harness.contract` and
  `agent_harness.execution`; `scripts/check-wheel.sh` green.

## Decisions (2026-10-06)

1. API as proposed: `agent_harness.execution` with `launch`/`Execution`, `ScriptedBackend`, `ProcessBackend`;
   ADR 0004.
2. Contract v1 kept: offline backends report `qualified: false`, `launch_ready: false`, `refusal:
   NOT_QUALIFIED` ("not ready for a qualified launch") and still serve requests without `qualified_isolation`.
3. Implement first; ADR 0004 carries a consumer delta for agent-benchmark, whose review follows.

## Evidence (2026-10-06)

Environment: macOS 24.6.0 (x86_64), CPython 3.13.16. Code: `src/agent_harness/execution.py`, ADR 0004.

- AC1: `tests/test_execution.py` validates every returned result against its request: completed (2 attempts,
  usage `complete`, candidate sealed and digest-checked), error, timeout, unknown, cancel, rejected.
- AC2: a real child that spawned a grandchild is cancelled: `outcome: cancel`, `cancel_requested: true`,
  `drain: confirmed`, and the grandchild PID no longer exists; with the group observed alive, `drain:
  unconfirmed`; a 1 s timeout gives `timeout` with `drain: unconfirmed`; exit code 3 and a sealed output file
  are reported for a completed process; a missing binary is `error`/`LAUNCH_FAILED`.
- AC3: `qualified_isolation` → `NOT_QUALIFIED`, `usage` on the process backend → `CAPABILITY_UNSUPPORTED`; no
  process started (marker file absent) and the evidence root stays empty.
- AC4: a relaunch returns the stored result; a changed request with the same ID raises `BINDING_MISMATCH`; a
  start marker without a result reconciles to `unknown` and the process is not run.
- Mutation checks, each caught by the suite: no group kill on cancel, timeout allowed to confirm drain,
  missing capability launched, binding check disabled, result not validated.
- AC5: `examples/minimal-consumer/consumer.py` uses only `agent_harness.contract` and `agent_harness.execution`
  (scripted launch with sealed candidate; process cancel with confirmed drain) from an empty directory against
  the installed wheel; `scripts/check-wheel.sh`: 121 tests OK, 1 skipped (CI-only benchmark).
- Limits (ADR 0004): descendants leaving the process group are not tracked; not a sandbox; never qualified.

## Independent evaluation (2026-10-06)

Fresh-context evaluator verdict on `7dd7f8e`: **fail**. AC2, AC3 and AC5 held. Findings and fixes:

- High: a contract-valid `request_id` containing `/` and `..` wrote records and the candidate outside the
  evidence root (the ID was a filename). Fix: records and candidates are named by the sha256 of the ID.
- High: a second launcher that recorded `unknown` while the first ran made the first thread die on
  `FileExistsError` before finishing, so `result()` hung forever and the in-process registry leaked. Fix: the
  run always finishes and unregisters (`try/finally`); the first published record wins and is returned; the
  registry key uses the resolved evidence root.
- Medium: running children outlived the interpreter (own session, daemon thread). Fix: an exit hook cancels
  running executions; ADR 0004 states that a hard kill of the caller still leaves them.
- Medium: five mutations survived (SIGTERM-only, SIGKILL-only, EPERM as gone, cancel flag lost on the unknown
  path, overwrite instead of create-once, no lock). Fix: tests for graceful SIGTERM then SIGKILL of a
  SIGTERM-ignoring descendant, EPERM, cancel flag after a broken backend, create-once, and 20 concurrent
  launches of one request (one run, real outcome). All ten mutations listed below are caught.
- Low: `drain: confirmed` after a `setsid` escape is now stated as covering the process group only (ADR 0004).
- Smaller: stored results are validated against the request (corrupt → `MALFORMED`); a changed request reusing
  an ID is refused before capability checks; a failed temp write leaves no file; candidate paths must stay in
  the workspace (`..` refused, symlink out not sealed).

Mutations caught after the fixes (each on a copy of `src`): SIGTERM only, SIGKILL only, EPERM as gone, cancel
flag lost, not create-once, no lock (5/5 runs), raw ID as filename, race record ignored, symlink escape sealed,
no exit hook. Suite stable 5/5. `scripts/check-wheel.sh`: 131 tests OK, 1 skipped.

## Re-evaluation (2026-10-06)

Fresh-context evaluator verdict on `fed9f09`: **pass** — the six findings are closed (escape, race in and
across processes, exit cleanup, mutations, `setsid` wording, smaller items); no new high-severity defect.
Low findings fixed afterwards: the exit hook uses one 30 s deadline for all executions; a forked child no
longer inherits running executions (`os.register_at_fork`); a corrupt start marker is `MALFORMED`; new tests
for a stored result of another request and a changed request while running (both mutations caught). Recorded
as limits in ADR 0004: spellings of one evidence root that `resolve()` does not fold (case-insensitive volume,
symlinked `executions/`) behave like two processes. Pending: agent-benchmark review of ADR 0004.

## Consumer review follow-up (2026-10-06)

agent-benchmark accepted deltas 1–4 and asked to settle three points before tagging; done in `b1dd552`:
`__version__` `0.2.0`; `launch(request, backend, *, workspace, evidence_root)` with no `RepoContext`
(digest-pinned bundles have no Git base, and `repo_id`/`base_sha`/`authority_root` were unused);
`ScriptedBackend(rejection=...)` for `BACKEND_UNAVAILABLE`. New tests: path validation, no repository
arguments accepted, scripted rejection without side effects. `scripts/check-wheel.sh`: 135 tests OK,
1 skipped. Pinning waits for the `v0.2.0` tag.
