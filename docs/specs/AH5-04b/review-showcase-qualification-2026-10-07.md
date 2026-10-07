# Harness review of Showcase AH5-04B-QUAL-001 T-001…T-005 (2026-10-07)

- Scope: read-only review against the shared contract, the qualification report (ADR 0002) and the B1–B10
  definitions (`grading-requirements.md`). Integration (merging the task branches) belongs to Showcase's owner;
  Harness changes nothing in Showcase.
- Branch shape: T-001 (`11adcca`, base `218bfa1`) and T-002 (`4b8a95e`, base `00c7f1e`) are independent;
  T-003 → T-004 → T-005 (`81e6823` → `70206b4` → `fe5c639`) is one chain.

## Report (T-005, `fe5c639`)

`docs/specs/AH5-04B-QUAL-001/evidence/docker-desktop-rerun-2026-10-07-run4/report.json`, file sha256
`dd27ded6…a8228` (matches the handoff), `qualification_digest` `sha256:08eda879…98885`. With Harness `v0.3.0`:

```text
agent-harness qualification --check report.json --evidence-root evidence \
  --capability-report capability.json --job-id showcase-docker-desktop-local-2026-10-07-run4
→ valid, does not pass (exit 1)
```

25 checks pass with job-bound evidence of their own; B10 is `not-run`; `independent_review` is null; the capability
report is `qualified: false` and binds to the same target, policy digest and job. Not qualified, correctly.

## Per commit

| Task | Verdict | Notes |
|---|---|---|
| T-001 `11adcca` | ok | `chmod 0o777` only inside a `TemporaryDirectory` (owner-only parent), so no other host user reaches it; regression test reproduces the umask failure. |
| T-002 `4b8a95e` | ok, one condition | Exception messages enter committed evidence: bound their length and keep paths/values that could hold secrets out (constitution rule 11). |
| T-003 `81e6823` | ok, two points | Limits match B8 (512 MiB no swap, 64 PIDs, 1 vCPU, no network, read-only root, ENOSPC at 256 MiB, 1 MiB host-side output cap with truncation flag). (1) B8's 256 MiB is workspace **plus** `/tmp`; the probe allows 256 MiB + a 16 MiB `/tmp`. (2) On a timeout `proc.kill()` kills the `docker` client, not necessarily the container; B8 requires the sandbox killed — stop the container by ID (or run with `--stop-timeout`/`docker kill`) and take the evidence from container state. |
| T-004 `70206b4` | ok, one point | `job_id` = run ID + attempt (whole-token matching keeps `-attempt-1` and `-attempt-10` apart); `contents: read`; job-bound artifact upload. Pin `actions/checkout`, `setup-python`, `upload-artifact` by commit SHA, as the other Showcase workflows do. |
| T-005 `fe5c639` | ok | Evidence and report consistent with the checker; NOT QUALIFIED stated. |

## B10 needs contract v2 now

B10 (per-grade target ID, qualification report digest, workload image digest, applied limits, fired limit, exit
code) is exactly what contract v1 cannot carry (ADR 0005). ADR 0005 deferred implementation until a target
qualifies, but no target can pass B10 without it, so the deferral is a deadlock: Harness implements contract v2
next (ADR 0005 shape, agreed with agent-benchmark), and Showcase re-runs B10 against it.
