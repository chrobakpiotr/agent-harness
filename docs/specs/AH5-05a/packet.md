# AH5-05a — Offline launch/cancel API (fake and controlled backends)

- Status: **accepted**.
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
