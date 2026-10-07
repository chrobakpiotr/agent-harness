# Contract v2 — target evidence and resource limits (ADR 0005)

- Status: **accepted** and **done** (executed confirmation 2026-10-07: 150/150 mutants killed). B10 of the 04b qualification cannot pass without per-grade target and
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

## Re-check (2026-10-07)

Verdict on `b29dfdf`: **fail** on two narrow points, both fixed: (1) "limits only when the request sets limits"
had no isolated test — added (removing the rule now fails the suite); (2) a backend claiming `qualified` without a
`target` could publish an invalid `unknown` result that broke every later launch of that request ID — `launch` now
refuses such a backend before anything is written (tested; removing the guard fails the suite). Confirmed fixed:
exact applied limits (bool, float, 2**64, missing/extra keys, non-dict), v1 `missing-qualification` still valid,
qualified v2 requests need limits, a non-qualified backend cannot inject a target (falls back to `unknown`).

## Final sweep (2026-10-07)

Verdict on `54c21c9`: **fail** — the qualification-digest and `fired`-value checks had no test, and the qualified
guard accepted a malformed target (an invalid `unknown` could still be published). Fixed: `contract.validate_target`
is shared by the validator and `launch`, which refuses a qualified backend whose target is missing or malformed
before writing anything (tested for `None`, `{}` and a bad digest); tests for the qualification digest, `fired`
value, timeout-only limits without requested limits and the v1 `versions` map. A sweep that turns each validator
call of the v2 code into a no-op, one at a time, against `test_contract_v2`, `test_contract` and `test_execution`
leaves **0 survivors**.

## Fourth check (2026-10-07)

Verdict on `e831e99`: **fail** — the evaluator's AST sweep (124 mutants: dropped statements, forced conditions,
dropped `and`/`or` operands, negated comparisons, unwrapped validators) left two v2 survivors: the `fired` value
check (its test was also caught by the LIMIT_EXCEEDED rule) and the qualified-limits condition (the no-limits test
did not assert the `completed` outcome). Fixed with isolated tests; the dead `or {}` was removed; v1 rules shared
by v2 results (a rejected result was never launched, the outcome value) are now pinned too. Re-running that sweep
on the current code: 122 of 124 killed; the two survivors drop docstrings.
- Confirmation attempt (2026-10-07): incomplete — the evaluator's tools were blocked (safety classifier
  unavailable), so it traced 52 mutants statically; one likely survivor (a v1 result carrying `target`/`limits`) is
  now tested and that mutation fails the suite. An executed independent confirmation is still pending.

## Executed confirmation (2026-10-07)

Fresh-context evaluator, own AST sweep (150 mutants over the v2 code and the qualified-backend guard): 148 killed.
Survivors: a result `contract_version` of `True` passed when `_doc_version` was removed (`True == 1`), and the
dropped `return target` of the public `validate_target`. Fixed with tests only (no source change): a result
version of `True`, `1.0` or missing is refused; `validate_target` returns its argument. The evaluator's sweep
script, re-run unchanged on the current code: **150 of 150 killed**. `scripts/check-wheel.sh` green.

## Amendment: candidate targets (2026-10-07)

Showcase evidence `b10-contract-v2-blocker-2026-10-07.json`: B10 was circular for the first qualification. Decided:
candidate targets (ADR 0005 amendment). Rules: `qualified` ⇒ target with a `qualification_digest`; `unqualified` ⇒
optional candidate target with `qualification_digest: null`; `fake`/`controlled` ⇒ no target. Tests: the candidate
fixture validates; a candidate claiming a digest, a qualified target without one, a target on `fake`/`controlled`
are refused; an unqualified result without a target is valid; `validate_target` defaults to qualified. Statement
sweep: 0 survivors; AST sweep over `validate_target` and `_target_and_limits`: all killed (remaining survivors lie in
the capability/qualification-report validators, covered by `test_qualification`, outside this sweep's test set).
Independent check (2026-10-07): **pass** — circularity broken without weakening (candidate claiming a digest,
qualified without a digest, target on fake/controlled, candidate answering a qualified request, rejected with a target:
all refused); v0.4 fixtures valid; own AST sweep 109/112 killed, the 3 survivors equivalent (docstrings, `fired`
default). Noted in ADR 0005: an `unknown` terminal of an unqualified launch carries no target.
