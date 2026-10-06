# AH5-04b — Grading boundary requirements for the qualified target (consumer input)

- Status: **input to Showcase's 04b qualification** (owner: Showcase, ADR 0001). Harness owns the contract
  mapping below; it implements no backend.
- Source: agent-benchmark `29889c2`, `docs/specs/ab5-offline-mvp/ab5-05b-grading-boundary.md` (B1–B10, measured
  baseline for `reference-001`). Showcase handoff `8c7a251` (Q01–Q16 target qualification).

The first qualified target should serve both verification-v2 (Showcase) and benchmark grading, so each item below
is meant to become a qualification check with its own evidence in the same target report, not a second
qualification later.

| ID | Requirement (benchmark wording, abridged) | Qualification check | Contract v1 mapping |
|---|---|---|---|
| B1 | Hidden material absent from the execution sandbox (mounts, image layers, Git history, network, env) | canary planted in the hidden bundle is not found by a payload searching FS, history, env | none needed (target property) |
| B2 | Hidden expected values never enter the grading sandbox | canary expected value outside the sandbox stays unreadable | none needed |
| B3 | Fresh sandbox per grade, destroyed after; nothing persists | marker written in grade 1 is absent in grade 2 | none needed |
| B4 | Workspace rw; test inputs ro; root FS ro; `/tmp` tmpfs; no host home, Docker socket, SSH/GPG agent, `.git` | payload lists mounts, probes socket/agent paths | none needed |
| B5 | No network: no egress, DNS or metadata; loopback only | TCP/UDP/DNS/metadata attempts all fail | none needed |
| B6 | Environment exactly what the grader passes; nothing inherited; no credential files | launcher-env canary and host credential-file canary both invisible | none needed |
| B7 | Non-root, no capabilities, `no_new_privs`, default seccomp | setuid, `mount`, raw socket attempts fail | none needed |
| B8 | Limits enforced by the target: wall = `timeout_seconds`, 1 vCPU, 512 MiB no swap, 64 PIDs, 256 MiB writable disk; on a timeout/memory/PID breach the sandbox is killed and the result names the limit; disk exhaustion is observed and named; output beyond 1 MiB is dropped (not a kill) | payload exceeds each limit in turn; result names it; a large-output payload still completes with output truncated | **gap**: v1 `outcome` has only `timeout`; no applied limits, no `oom`/`pids`/`disk`, no truncation flag (ADR 0005) |
| B9 | One bounded output file (≤ 1 MiB) returned after the sandbox is gone; verdict computed outside | payload writes the file; caller receives exactly those bytes | `artifacts[]` (named, sealed by digest) |
| B10 | Evidence per grade: target ID, qualification report digest, image digest, applied limits, limit fired, exit code | present in every result | **gap**: v1 result has `isolation_level` and `exit_code` only; target ID and digests are in `CapabilityReport` / free-form `versions` |

Notes for the qualification owner:

- B8 defaults carry about 150× headroom over the measured baseline (0.19 s wall, ~0.16 s CPU, 16 MiB, 4 KB bundle);
  a larger task needs new measurements, not a silent raise (benchmark rule).
- B2/B9 exist because a candidate can forge a pass from inside the test process; the target's part is the trusted
  channel (B9) and the destroyed sandbox (B3), the verdict stays with the caller.
- Until a target passes, model-written code stays ungraded; Harness `ProcessBackend` (`controlled`) is not a
  sandbox and is never used for grading.
- Recording: each check is one entry of the contract v1 qualification report (ADR 0002, AH5-04b-r), checked with
  `agent-harness qualification --check report.json --evidence-root <dir>`.
- The B8/B10 gaps are proposed for contract v2 in ADR 0005; the target can record the evidence in its report
  meanwhile, but consumers cannot read it from a result until v2.
