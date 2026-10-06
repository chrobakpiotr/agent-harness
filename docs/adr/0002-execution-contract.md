# ADR 0002 — Execution contract v1

- Status: **Accepted**; shipped in v0.1.0
  (owner: agent-harness; consumer review by agent-benchmark done, see below)
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
2. Pinning → consumers pin a Git tag/SHA of this repository,
   `agent-harness @ git+https://github.com/chrobakpiotr/agent-harness.git@<sha>`. No PyPI.
3. No launch/cancel API yet → agreed, the benchmark keeps its fake port. Per the master plan the
   offline public API (fake/controlled backend) is due after AH5-03b, hardened live after AH5-04c
   (AH5-05); not AH5-04b.

## Consumer alignment: Showcase S30-06 (2026-10-06)

Showcase `c9a5fec`/`a6fac3c` define REF-CORRECTNESS, REF-PERFORMANCE and the external PROD-Q as separate
qualification targets. They are not harness execution targets today and need no contract change. If Showcase
later wants harness-managed reports for them, each is its own `CapabilityReport.target` with its own policy
digest and qualification; one target's report never qualifies another, and parent-spec completion stays
Showcase lifecycle, outside this contract.

## Qualification report (2026-10-06, AH5-04b-r)

An additive v1 document; no existing schema changes. `validate_qualification_report` covers one exact target
tuple and one job: `target`, `policy_digest`, `author`, `tuple` (must state `job_id`, `host`, `kernel`, `engine` and
`workload_image` as a sha256 digest; more facts allowed), `checks` (exactly one per mandatory check
`QUALIFICATION_CHECKS` = Q01–Q16 and B1–B10, each `pass`/`fail`/`not-run`; a pass needs evidence of its own, not
shared with another check), `independent_review` (nullable; `reviewer` ≠ `author`, `subject` =
`review_subject(report)` — the report digest without the review — and evidence distinct from check evidence) and
`created_at`.

- `qualification_passes(report, evidence_root)` re-hashes every evidence file inside the root, requires the
  evidence of each passing check to name the report's `job_id` and the review evidence to name its `subject`, and
  is true only when every check passes and the review passes. There is no unverified "passes". `reviewer` and
  `author` are compared case-insensitively.
- `validate_capability_binding(report, qualification, evidence_root, job_id)`: same target and policy digest,
  the caller's own job (`tuple.job_id`; a GitHub-hosted job is a fresh VM, so nothing stays qualified between
  jobs), and `qualified` only with a passing qualification. `validate_capability_report` alone checks shape only
  and does not make `qualified` meaningful; neither does `isolation_level: qualified` in a v1 result (ADR 0005
  proposes a result `target`). Consumers that rely on qualification call the binding.
- CLI: `agent-harness qualification --check FILE --evidence-root DIR [--capability-report FILE --job-id ID]`:
  0 passing, 1 not passing or not bindable, 2 invalid or unreadable — any JSON error, duplicate keys, excessive
  nesting, missing or tampered evidence, or misuse (`--job-id` and `--capability-report` only together); the
  message names the file at fault. Only the exit code is authoritative.
- `qualification_digest` is the value ADR 0005 proposes for results. Showcase owns the probes, runs and raw
  evidence; `contract_fixtures/qualification-example.json` is a format example of a fictitious target whose
  review fails and whose evidence is not shipped.
- **Authenticity is out of scope.** The report, its review and its `job_id` are claims of whoever produces the
  files: binding to the job and subject makes accidental carry-over to another job or report fail, but an author
  who regenerates the evidence can still produce a passing report, and distinct but fabricated evidence cannot be
  told apart from real evidence. Trust comes from where the report is produced (the qualifying CI job and an
  independent reviewer agent); signed reviews (e.g. Ed25519, as the `grants` extra already verifies) are a future
  option, not part of this version.
- Limits: the digest covers the parsed document (the CLI rejects duplicate keys); evidence paths are compared as
  given (no Unicode normalisation); evidence files are resolved and then opened, so a swap between the two
  checks is not excluded on a writable evidence root.

## Not decided here

Python API for launching/cancelling (no implementation yet; arrives with a real backend in AH5-04b),
retry policy, retention of artefacts.
