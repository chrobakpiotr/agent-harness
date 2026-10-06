# ADR 0004 — Offline launch/cancel API

- Status: **Accepted** (AH5-05a); consumer review by agent-benchmark pending
- Date: 2026-10-06
- Code: `src/agent_harness/execution.py`; tests `tests/test_execution.py`
- Inputs: ADR 0002 ("Not decided here": launch/cancel API), agent-benchmark `docs/execution-port.md`
  (open item 3, AB5-06b), master plan AH5-05.

## Decision

`agent_harness.execution` joins `agent_harness.contract` and the CLI as public API. Contract v1 is unchanged.

- `launch(request, repo, backend) -> Execution` validates the request and the explicit `RepoContext`.
  `Execution.cancel()` asks the backend to stop; `Execution.result(timeout=None)` returns the terminal result,
  always validated against its request. A backend that raises or returns an invalid result yields `unknown`
  (drain `unconfirmed`), never success.
- **Refusal without side effects.** A required capability the backend lacks gives `rejected`
  (`NOT_QUALIFIED` for `qualified_isolation`, else `CAPABILITY_UNSUPPORTED`): nothing started, nothing written.
- **One request ID, one execution.** A start marker and the terminal result are published create-once under
  `<evidence_root>/executions/`, named by the sha256 of the request ID (IDs may contain `/`). The first
  record wins: a launcher that finds a start without a result records `unknown`, and the original launch then
  returns that record. A stored result is validated against its request when read. Relaunching an ID returns
  the stored result; a launch interrupted before its result (crash) reconciles to `unknown` and is never rerun;
  a different request reusing the ID is `BINDING_MISMATCH`, checked before any capability refusal. In one
  process, a relaunch while running returns the running `Execution`.
- **Candidate files.** `ProcessBackend(candidate=...)` must be a relative path without `..`; a file resolving
  outside the workspace (symlink) is not sealed.
- **Backends.** `ScriptedBackend` — deterministic attempts, usage summaries and candidate bytes;
  `isolation_level: fake`; capabilities `cancel`, `usage`. `ProcessBackend(argv)` — a real child in
  `repo.workspace` in its own process group; timeout and cancel send SIGTERM then SIGKILL to the group;
  `drain: confirmed` only when the group is observed empty (a timeout never confirms); a declared output file
  is sealed into the evidence root by digest; usage is not observed; `isolation_level: controlled`;
  capability `cancel`; POSIX only.
- **Capability reports (v1 kept).** Both backends report `discovered`/`supported`, `qualified: false`,
  `launch_ready: false`, `refusal: NOT_QUALIFIED`: in v1 `launch_ready` means ready for a *qualified*
  launch. They still serve requests that do not require `qualified_isolation`.

## Limits

- `drain: confirmed` covers the process group only. A descendant that leaves it (`setsid`) is not tracked and
  can outlive a confirmed drain; `ProcessBackend` is not a sandbox and never qualified. Qualified isolation is
  AH5-04b/04c.
- Executions still running when the interpreter exits are cancelled by an exit hook (their process groups
  are terminated, 30 s in total); a hard kill of the caller leaves them running, and a relaunch then reports
  `unknown`. A forked child does not inherit the parent's running executions.
- One process recognises a relaunch by the resolved evidence root. Two spellings that the filesystem treats as
  the same directory but `resolve()` does not (case-insensitive volumes, two roots symlinking one
  `executions/`) behave like two processes: the first published record wins, which can be `unknown`. Use one
  canonical evidence root per repository.
- No real providers, retries or usage capture for processes; no artefact retention policy.
- The child inherits the caller's environment unless `env` is given; callers keep secrets out of it.

## Consumer delta for agent-benchmark (to apply in its own repo, not here)

- `fake_backend(...)` → `execution.launch(request, repo, execution.ScriptedBackend(...))` with a
  `RepoContext` whose `evidence_root` is the trial evidence root; the candidate reference is then produced
  by the harness, so `import_result` keeps verifying it as today.
- The AB5-06a "cancellation tied to real terminal/drain" row can use `ProcessBackend` with a request that
  lists `cancel` (not `usage`): `outcome: cancel`, `cancel_requested: true`, `drain` from the process group.
- Retrying a crashed trial with the same `request_id` returns `unknown` instead of a second run; a new attempt
  needs a new `request_id`.
- Pin: a tag containing this module (after review), never `main`.
