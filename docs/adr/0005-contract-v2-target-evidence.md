# ADR 0005 — Contract v2: target evidence and resource limits in results

- Status: **Proposed** (needs a decision and agent-benchmark consumer review; nothing implemented)
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
- Request `limits` (nullable): `{"wall_seconds", "cpus", "memory_bytes", "pids", "disk_bytes", "output_bytes"}`
  as integers; result `limits`: `{"applied": {...same keys...}, "fired": null | "timeout" | "oom" | "pids" |
  "disk" | "output"}`. A launch that cannot enforce a requested limit is `rejected` (`CAPABILITY_UNSUPPORTED`),
  never silently unlimited. `fired: "timeout"` iff `outcome: timeout`; any other fired limit gives
  `outcome: error`, `error_code: LIMIT_EXCEEDED` (new code), `drain` from the target.
- `isolation_level: qualified` requires a non-null `target`; other levels require `target: null`.
- v1 stays supported for reading; producers emit one version per document; consumers pin a release.

## Open questions

1. Is `LIMIT_EXCEEDED` with `limits.fired` preferable to new `outcome` values (`oom`, …)? The proposal keeps the
   outcome set small and puts the cause in `limits.fired`.
2. Do offline backends (`fake`/`controlled`) accept `limits`? Proposal: `ScriptedBackend` may script `fired`;
   `ProcessBackend` rejects a request with limits (it cannot enforce them).
3. Timing: v2 is only useful once a target qualifies (AH5-04b); agree the shape now so the qualification records
   the same evidence.
