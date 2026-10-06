# ADR 0005 — Contract v2: target evidence and resource limits in results

- Status: **Proposed** — shape agreed in the agent-benchmark consumer review (2026-10-06, below); to be
  implemented after a target qualifies (AH5-04b). Nothing implemented.
- Date: 2026-10-06
- Inputs: agent-benchmark AB5-05b B8/B10 (`29889c2`); `docs/specs/AH5-04b/grading-requirements.md`; ADR 0002.

## Problem

A qualified launch must let a consumer prove, from the result alone, which qualified target ran it and which
resource limit (if any) ended it. Contract v1 cannot: `outcome` distinguishes only `timeout`, applied limits have
no field, and the target identity and qualification digest live only in a separate `CapabilityReport` (or in the
free-form `versions` map, which carries no meaning). v1 schemas are closed, so adding fields is a new version.

## Proposal

`contract_version: 2` for request and result (capability report unchanged in shape):

- Result `target`: `null` for non-qualified launches, else `{"id", "qualification_digest", "image_digest"}` — the
  `CapabilityReport.target` and `policy_digest` of the passing report, plus the workload image digest.
- Request `limits` (nullable): `{"cpus", "memory_bytes", "pids", "disk_bytes", "output_bytes"}` as integers.
  Wall time stays `timeout_seconds` (no second wall limit). Result `limits`: `{"applied": {...same keys...,
  "timeout_seconds"}, "fired": null | "timeout" | "oom" | "pids" | "disk", "output_truncated": bool}`.
- `applied` equals the request exactly; a target that cannot enforce a value exactly rejects the request
  (`CAPABILITY_UNSUPPORTED`), never rounds it and never runs silently unlimited.
- Output is not a kill limit: output beyond `output_bytes` is dropped and `output_truncated` is true. `fired`
  names a limit the target observed ending or impairing the run (`disk` usually surfaces as write errors);
  `fired: null` does not prove that no limit was reached.
- The validator enforces both directions: `error_code: LIMIT_EXCEEDED` (new code) iff `fired` is non-null and not
  `timeout` (with `outcome: error`); `fired: timeout` iff `outcome: timeout`.
- `isolation_level: qualified` requires a non-null `target` and non-null `limits` (no hidden defaults); other
  levels require `target: null`.
- Grading is a launch like any other: a grading request binds the candidate and test-input digests in
  `input_bindings`, and its verdict file returns through `artifacts[]`; a target's qualification covers grading
  launches as well as agent execution.
- v1 stays supported for reading; producers emit one version per document; consumers pin a release.

## Consumer review (agent-benchmark, 2026-10-06, on `f95a026`)

1. `LIMIT_EXCEEDED` plus `limits.fired`, not new outcome values: outcome-based reports and predeclared exclusions
   stay unchanged.
2. Offline backends: `ScriptedBackend` can script `fired` (so consumers test limit handling offline);
   `ProcessBackend` rejects any non-null `limits`.
3. Shape fixed now, implemented after qualification.
4. Gaps adopted above: one wall limit (`timeout_seconds`), output truncation instead of an output kill, `fired`
   as observed cause, explicit limits required for qualified launches, `applied` equal to the request.
5. Consumer side when v2 ships: agent-benchmark carries `target` and `limits` in its trial and grade records.
