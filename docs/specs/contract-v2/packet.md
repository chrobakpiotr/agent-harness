# Contract v2 — target evidence and resource limits (ADR 0005)

- Status: **accepted** (ordered 2026-10-07): B10 of the 04b qualification cannot pass without per-grade target and
  limit evidence, so implementation no longer waits for a qualified target.
- Source: ADR 0005 (shape agreed in the agent-benchmark review); Showcase review
  `docs/specs/AH5-04b/review-showcase-qualification-2026-10-07.md`.

## Scope

- `contract_version` 2 for request and result; v1 documents stay valid and are validated by v1 rules. A result
  carries its request's version; usage events carry the document's version.
- Request v2: v1 fields + `limits` (null or `{cpus, memory_bytes, pids, disk_bytes, output_bytes}`, integers ≥ 1).
- Result v2: v1 fields + `target` (null or `{id, qualification_digest, image_digest}`) + `limits` (null or
  `{applied, fired, output_truncated}`); `applied` = the request's limits plus `timeout_seconds`, exactly.
- Rules: `fired` ∈ null/`timeout`/`oom`/`pids`/`disk`; `fired: timeout` iff `outcome: timeout`; `error_code:
  LIMIT_EXCEEDED` (v2 only) iff `fired` ∈ `oom`/`pids`/`disk`, with `outcome: error`; request limits null ⇔ result
  limits null; `isolation_level: qualified` requires non-null `target` and `limits`; other levels require
  `target: null`; a rejected result has neither.
- `agent_harness.execution`: v1 requests behave as today. A v2 request with limits is rejected
  (`CAPABILITY_UNSUPPORTED`, nothing started) by `ProcessBackend`; `ScriptedBackend(fired=…, output_truncated=…)`
  answers it with applied limits echoed and the scripted cause.
- CapabilityReport and QualificationReport shapes unchanged (version 1).

## Acceptance criteria

- AC1: every rule above is enforced in both directions; v1 documents and fixtures unchanged and still valid.
- AC2: offline behaviour: process backend rejects limits without side effects; scripted backend produces valid v2
  results for each `fired` value and output truncation.
- AC3: a v2 golden fixture (qualified grading launch ended by a fired limit, fictitious target) ships and validates.
- AC4: `scripts/check-wheel.sh` green; the minimal consumer still runs.

## Evidence (2026-10-07)

- AC1: `tests/test_contract_v2.py`: the v2 fixture validates; v1 fixtures still validate, a v1 request with `limits`
  and a v1 result with `LIMIT_EXCEEDED` are malformed; a v1 result for a v2 request and version 3 are
  `VERSION_MISMATCH`; `LIMIT_EXCEEDED` without a fired limit, a fired limit without the code, `oom` with outcome
  `completed`, `timeout` fired without outcome timeout and outcome timeout without `fired: timeout`, rounded
  `memory_bytes`/`timeout_seconds`, limits without requested limits, qualified without target or limits, a target
  on a `fake` launch, and a rejected result with a target are all refused; `unknown` with null limits and a rejected
  result without target/limits are accepted. Mutations caught: LIMIT_EXCEEDED rule, timeout rule, applied rule,
  target rule, version match. A separate "fired limit ⇒ outcome error" check was unreachable (implied by the
  error-code rules) and was removed.
- AC2: `ProcessBackend` rejects a v2 request with limits (`CAPABILITY_UNSUPPORTED`, no process, nothing written);
  `ScriptedBackend` answers with applied limits echoed for `oom`/`pids`/`disk` (`LIMIT_EXCEEDED`), `timeout` and
  none, with `output_truncated`; a v2 request without limits gives `limits: null`; an inconsistent script (oom with
  outcome completed) becomes `unknown`.
- AC4: `scripts/check-wheel.sh`: 159 tests OK, 1 skipped; the minimal consumer runs.
- AC3: `contract_fixtures/v2-limit-exceeded.json` (qualified grading launch on `example-not-a-real-target`, oom,
  verdict input in `artifacts[]`) is pinned in the golden fixture set.

## Independent evaluation (2026-10-07)

Fresh-context verdict on `f2eeda2`: **fail** — `applied` compared with `==`, so `cpus: True` and float values
passed; ten rules were enforced but not pinned by an isolated test (their mutations survived). Fixed: each applied
value must be an `int` equal to the request's; a v2 request with `qualified_isolation` needs explicit limits;
backends can state their qualified target (an `unknown` fallback of a qualified backend keeps it), so the execution
layer can produce valid qualified results. One isolated test per rule now: rejected with limits, qualified without
limits, missing limits when requested, `output_truncated` type, target fields/id/image digest, result-limits fields,
applied `True`/float/extra key, usage-event version, request limits ≥ 1 and exact keys, qualified request without
limits — each of the 13 mutations fails the suite. Consumer note: agent-benchmark checks `error_code` against
`ERROR_CODES`, which deliberately excludes `LIMIT_EXCEEDED`; it adds the v2 code when it adopts v2.
