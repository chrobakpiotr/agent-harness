# ADR 0002 — Execution contract v1

- Status: **Proposed** (owner: agent-harness; consumer review by agent-benchmark pending)
- Date: 2026-10-05
- Code: `src/agent_harness/contract.py`; fixtures shipped in the wheel under
  `agent_harness/contract_fixtures/` (success, fail, timeout, unknown-terminal, missing-qualification).
- Inputs: master plan AH5-02; benchmark requirements `agent-benchmark/docs/execution-port.md` (AB5-01).

## Decision

Documents are plain JSON dicts validated by stdlib functions: `validate_request`,
`validate_result(result, request)`, `validate_capability_report`, `validate_repo_context`,
`request_digest`. Errors raise `ContractError` with a bounded `code`:
`MALFORMED`, `VERSION_MISMATCH`, `BINDING_MISMATCH`, `UNSAFE_PATH`.

- **Version.** `contract_version` is an integer, exactly `1`, on every document and usage event.
  Any other value is `VERSION_MISMATCH`. Schemas are closed; adding a field is a new version.
- **Binding.** A result carries `request_id` and `request_digest` (sha256 of canonical JSON of the
  request). Any change to the request after launch is `BINDING_MISMATCH`. A request needing
  `qualified_isolation` cannot be answered by a launch at any other `isolation_level`.
- **Three states.** `outcome` (`completed/error/timeout/cancel/unknown/rejected`) plus `exit_code`
  describe execution only. `completion` (`accepted/rejected/null`) is the harness decision and may be
  non-null only for `completed`. The grade belongs to the consumer and is not in the contract.
- **Never launched.** `rejected` has `error_code` and no execution ID, attempts, times, drain, outputs
  or usage. Retrying a rejected request has no side effects to reconcile.
- **Cancellation.** `cancel_requested` and `drain` (`confirmed/unconfirmed`) are separate facts.
  `unknown` always has `drain: unconfirmed`; a timeout does not prove descendants are gone.
- **Usage (open point 1).** Events have `event_id`, `attempt_id`, `source`, `kind` (`stream/summary`),
  nullable integer units (`input/output/cache_read/cache_write_tokens`) and `cache_semantics`.
  At most one `summary` per attempt; if present it is authoritative for that attempt and `stream`
  events are informational. Dedup key: `(execution_id, event_id)`. No events ⇒
  `usage_completeness: unknown` (unknown ≠ 0). A null unit always means unknown; 0 means the
  provider reported none. `complete` requires a summary for every attempt with every unit an
  integer; otherwise the result is `partial` or `unknown`. No prices.
- **Candidate (open point 2).** Reference, not bytes: `{path, sha256, size}`, path relative to the
  evidence root. The harness seals (computes the digest). Paths are relative POSIX with no empty,
  `.` or `..` segments, no backslash/NUL, ≤ 255 chars, else `UNSAFE_PATH`.
- **Attempts (open point 3).** `attempts[]` (1..`max_attempts`) with own IDs, outcome and times; every
  usage event names an existing `attempt_id`.
- **Capabilities (open point 4).** Request lists required `capabilities`
  (`cancel/usage/qualified_isolation`). Missing support is a `rejected` result with
  `CAPABILITY_UNSUPPORTED` or `NOT_QUALIFIED`. `CapabilityReport` keeps `discovered ⇐ supported ⇐
  qualified ⇐ launch_ready` as separate booleans, a `policy_digest` required once qualified, and a
  `refusal` code exactly when not launch-ready.
- **No secrets.** No free-text fields: IDs, model names, versions and settings keys match
  `[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}`; no error message, log, stderr or environment field.
  Limitation: a secret shaped like an ID (or put in a settings value) cannot be detected
  structurally; callers must not put secrets there.
- **RepoContext.** Explicit `repo_id`, `base_sha`, absolute `workspace`, `authority_root`,
  `evidence_root`; never inferred from the module location or cwd. Used by the harness (AH5-03b),
  not part of the request.

## Consumer delta for agent-benchmark (to apply in its own repo, not here)

Benchmark fake today differs in: `harness_completion: success/failure` → `completion: accepted/rejected`;
candidate bytes → candidate reference; new outcome `rejected`; `usage_event_id`/`usage` →
`event_id`/`units` + `contract_version`, `source`, `cache_semantics`.

## Consumer review (agent-benchmark, 2026-10-05)

1. Null units under `complete` were ambiguous → fixed by the `complete` rule above; the success
   fixture now reports cache units as 0.
2. Pinning/distribution form of the wheel → open.
3. No launch/cancel API yet → agreed, the benchmark keeps its fake port. Per the master plan the
   offline public API (fake/controlled backend) is due after AH5-03b, hardened live after AH5-04c
   (AH5-05); not AH5-04b.

## Not decided here

Python API for launching/cancelling (no implementation yet; arrives with a real backend in AH5-04b),
retry policy, retention of artefacts.
