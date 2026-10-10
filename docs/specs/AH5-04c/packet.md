# AH5-04c — Grading on the qualified target (`QualifiedDockerBackend`)

- Status: **accepted by agent-benchmark** (review of `9528dcb`, 2026-10-10); owner decisions D1–D5 below. Work
  split: AH5-04c-1 (probes into Harness, `qualify`), ADR 0002 amendment (review per tuple), AH5-04c-2 (backend).
- Source: master plan AH5-04c ("hardened live"); agent-benchmark AB5-07 (blocking: grading model-written code
  needs a qualified target); AH5-04b accepted report `sha256:f7cdab99…2600` (target
  `showcase-docker-desktop-linux-guest`, job `local-20261008T205033Z-7939417fefb5`), whose capability report says
  `qualified: true`, `launch_ready: false`, `BACKEND_UNAVAILABLE` because no backend launches into it.
- Constraints: B1–B10 (`docs/specs/AH5-04b/grading-requirements.md`); ADR 0002 (qualification is bound to one job);
  ADR 0005 (contract v2 target and limits). Harness owns the backend; Showcase owns the qualification probes and the
  image/policy definition (ADR 0001).

## Proposed scope

`agent_harness.execution.QualifiedDockerBackend(qualification, *, evidence_root, capability_report, job_id,
command, docker="docker")`. `launch` keeps its signature; contract v2 only.

**Input.**
- `workspace`: the candidate workspace with the candidate patch already applied by the caller.
- One read-only input file, `cases.json` (inputs only).
- Expected values never enter the container (B2): the verdict is computed by the caller from the answers file.

**Run.** One fresh container per request, with exactly the flags 04b qualified:
- `--network=none`, `--read-only`, `--user=65532:65532`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`;
- `--pids-limit=64`, `--memory=512m`, `--memory-swap=512m`, `--cpus=1`;
- `/tmp` as a 16 MiB tmpfs, `/dev/shm` as a 1 MiB read-only tmpfs;
- the pinned workload image by digest.

The workspace is streamed in as a tar on stdin and extracted into a 240 MiB tmpfs `/workspace`, so the qualified
256 MiB disk limit holds. `cases.json` is bind-mounted read-only at `/inputs/cases.json`. `command` (the caller's
grading argv, part of `config_digest`) runs with an empty environment plus what the caller passes (B6).

**Output (B9).**
- One output directory is bind-mounted read-write at `/output`.
- After exit, the container is removed by ID and its absence confirmed by `docker ps -a --filter id=` (B3).
- Only then is `/output/answers.json` read: no-follow, regular file only, at most 1 MiB. It is sealed as the named
  artifact `answers`.
- stdout/stderr are capped at 1 MiB host-side, with a truncation flag (B8).

**Limits and evidence (B8, B10).**
- The request's v2 `limits` must equal the qualified limits: cpus 1, memory 512 MiB, pids 64, disk 256 MiB,
  output 1 MiB. Anything else is `rejected` / `CAPABILITY_UNSUPPORTED`; a qualified target cannot enforce other
  values.
- Timeout kills the container by ID (`fired: timeout`); `OOMKilled` gives `fired: oom`.
- Results carry `isolation_level: qualified`, `target {id, image_digest, qualification_digest}`, `limits.applied`,
  `fired`, `output_truncated` and `exit_code`.

**Refusal before launch** (`rejected` with a reason, nothing started):
- `NOT_QUALIFIED` when the qualification does not pass, the capability binding fails (`validate_capability_binding`
  with the caller's `job_id`), or the live tuple differs from the report's: `docker version` engine, `docker info`
  host/kernel, local image digest.
- `BACKEND_UNAVAILABLE` when Docker is not reachable.

## Decisions needed

- **D1 — qualification per job. Decided: (a).** ADR 0002: "nothing stays qualified between jobs";
  `validate_capability_binding` requires the caller's `job_id`. The accepted 04b report qualifies only its own
  local job.
  - (a) **Recommended:** every grading session runs Showcase's qualification (26 checks, a few minutes on Docker
    Desktop) under a new session `job_id` first, and grades in that job. This is strict and needs no ADR change.
    It requires the Showcase qualification runner to be callable by the benchmark (Showcase-owned script).
  - (b) Amend ADR 0002 so a persistent local target stays qualified while the live tuple (engine, kernel, image,
    policy digest) is unchanged. Faster, but weaker: host changes that do not alter the tuple go unnoticed.
- **D2 — fired limits that the host cannot observe. Decided: `timeout`/`oom` only.** PIDs and disk exhaustion happen inside the container
  (`EAGAIN`/`ENOSPC`); the host sees only the exit code. Proposal: report `fired` only for `timeout` and `oom`, with
  `fired: null` otherwise. Alternatively, the grading command reports them in a sidecar file, which is untrusted
  output.
- **D3 — where `command` comes from:** the caller's argv in the backend config (proposed), or a trusted grader
  script in `/inputs` (read-only).

## Acceptance criteria (draft)

- AC1: a request is refused before launch (`NOT_QUALIFIED`, with a reason) when the qualification fails, the job
  differs or the live tuple differs; `CAPABILITY_UNSUPPORTED` for other limits.
- AC2: the run uses exactly the 04b-qualified flags. The test asserts the full `docker run` argv against the list
  recorded from `q_probes._base_args`.
- AC3: `answers` is read only after the container ID is confirmed absent; a symlink, non-regular file or file over
  1 MiB is not sealed.
- AC4: timeout and OOM give `fired: timeout` / `fired: oom`, with the container killed by ID; stdout over 1 MiB gives
  `output_truncated: true`.
- AC5: every result validates against contract v2 with `isolation_level: qualified` and the target's
  `qualification_digest`. A real run on the qualified Docker Desktop tuple passes one grading request end to end;
  the request carries no expected values.
- Tests: a fake `docker` on `PATH` for argv and lifecycle; one opt-in real-Docker test.

## Consumer review (agent-benchmark, 2026-10-10) and resulting decisions

- **D3 — command. Decided:** the caller's argv in the backend config, with the trusted driver inline:
  `["python3", "-c", <DRIVER>, "/inputs/cases.json", "/output/answers.json"]`. `<DRIVER>` is agent-benchmark's
  `grader.DRIVER`, covered by the grader identity digest and, through argv, by `config_digest`. Nothing from the
  candidate workspace is executed as a command; the driver only imports it. Working directory `/workspace`;
  environment exactly `PYTHONPATH=/workspace`, `PYTHONDONTWRITEBYTECODE=1`, `HOME=/tmp`.
- **Formats.**
  - `cases.json` (inputs only): `{"module": "...", "cases": [{"id": ..., "function": ..., "args": [...]}]}`.
  - `answers.json` (one JSON object, at most 1 MiB): `{"<case id>": {"value": <json>} | {"raises": [MRO class
    names]}}`.

  The verdict is computed by the benchmark (complete case set, exact count, equality with expected values kept
  outside); the exit code is evidence only.
- **Timeout:** `timeout_seconds` from the task bundle (30 s for real-001/reference-002), measured from container
  start to exit; streaming the workspace tar is not counted. Measured patch-io on the host: about 0.2 s.
- **D1 (a), refined:** one grading session = one `QualifiedDockerBackend` instance = one `job_id`, grading many
  requests (all candidates of one run). Qualification runs once per session, not per request.
- **D4 — `qualify(job_id)` in Harness. Decided:** move Showcase's qualification probes (`q_probes`, `q_lifecycle`,
  `b_probes`, `report`) into `agent_harness` (AH5-04c-1, with parity against Showcase's version; Showcase then uses
  the pinned library). `agent_harness.qualification.qualify(job_id, *, out)` runs the 26 checks and returns the
  report with its `qualification_digest`; the benchmark stores each session's report as evidence (B10). The
  benchmark does not depend on a Showcase checkout. Needs Showcase owner agreement (ADR 0001 per-module move).
- **D5 — independent review. Decided:** per tuple, not per job (ADR 0002 amendment). One independent review covers a
  target tuple (host, engine, kernel, workload image) plus the probe-code digest. A job's automatically produced
  report qualifies when all 26 checks pass with its own job-bound evidence and its tuple and probe-code digest equal
  a reviewed one. A new tuple or changed probe code needs a new review.
- **D2, accepted:** PID or disk exhaustion shows up as missing or wrong answers, i.e. FAIL; the benchmark records
  `fired` (timeout/oom) as a grading criterion.
- **Benchmark side:** the candidate patch is applied on the host with `git apply` on a fresh copy of `base/` (hardened
  git environment, patch scope checked: no symlinks, no `.git`, allow-listed files only). The container receives the
  finished workspace.

## Progress (2026-10-10)

- AH5-04c-2 backend: `execution.QualifiedDockerBackend` implemented (`01986e8`), with session mode via
  `reviewed=(report, evidence_root)`. 7 fake-docker tests cover flags, refusal on limits and tuple drift, session
  binding, timeout/OOM, unsafe or oversized answers, capped output and an unconfirmed destroy. The opt-in real run
  (`AGENT_HARNESS_REAL_DOCKER=1`) passes on Docker Desktop 29.8.2 / 7.0.14-linuxkit with the pinned image.
- ADR 0002 amendment (D5): `contract.session_qualification_passes`; `validate_capability_binding(…, reviewed=…)`.
- Open: AH5-04c-1, moving the probes and adding `qualify(job_id)` (awaits Showcase agreement); a reviewed reference
  run with `probe_digest`; independent evaluation; release.

## Independent evaluation of AH5-04c-2 (2026-10-10, `62826dc`): pass with conditions, fixed

| # | Finding | Fix |
|---|---|---|
| 1 high | A large (sparse) workspace stalled the session past the timeout and cancel: the tar was written before the deadline, and `tar -x` reads on after ENOSPC (real Docker: 1 GiB sparse blocked 34.9 s). | Workspaces whose apparent size exceeds the 240 MiB tmpfs are not launched (`error` / `LAUNCH_FAILED`). The stream runs in a thread under a 120 s budget; cancel applies throughout; the wall clock starts when the stream ends. Real Docker: 1 GiB sparse is refused in 0.6 s. |
| 2 medium | `/output` was an unbounded bind mount (real Docker: 400 MiB written, `completed`). | The host polls `/output` and kills the container above 1 MiB + 64 KiB: `error` / `LIMIT_EXCEEDED`, `fired: disk`. Real Docker: the 400 MiB writer is killed in 3.4 s. `/output` is outside the 04b-qualified mount set; the moved probes (AH5-04c-1) must qualify it. |
| 3 | Hardlinks in the workspace were followed. | Streamed members are directories and single-link regular files only. AH5-05b's diff candidate is withheld if the workspace holds a hardlink. |
| 4 | Kill and absence check went by name; stdout pipe left open. | Kill, inspect, `rm` and `ps --filter id=` go by the 64-hex container id; stdout is closed. The container environment is the image's ENV plus `GRADING_ENV`, with nothing from the host. |
| 5 | Untested guards. | New tests: the exact `docker run` argv (no extra flag or mount), session target/policy mismatch, tampered session evidence, engine-only drift, a directory `answers.json`, cancel during the stream, the oversized workspace, the hardlink. |

## AH5-04c-1 and the reviewed reference (2026-10-10)

- `agent_harness.qualification`: the probes moved verbatim (`provenance.md`, parity test on Git blob ids);
  `qualify(job_id, out, *, reviewed=None, timeout_seconds=30)`; `probe_digest()` covers the probe and
  orchestration modules; `policy_digest()` covers the flags, limits, grading environment, the backend's mount layout
  and entrypoint, and the check list. `qualification.reference.reviewed_reference(target)` returns the shipped
  reviewed reference.
- Independent evaluation of `qualify` and the session flow: pass with conditions, all fixed in `b18c8bc` (digest
  coverage, session pinned to the installed digests, out-dir symlink check before resolve, `reviewed` validated before
  any probe, unsafe `job_id` refused, flag-equality and digest tests).
- Reviewed reference: job `ref-20261010T194859Z`, all 26 checks pass, independent review
  `harness-independent-reviewer-20261010b` verdict pass, shipped as
  `qualification/reviewed/showcase-docker-desktop-linux-guest/` (digest `sha256:018ac943…`, probe digest
  `sha256:e3f5d77b…`, policy digest `sha256:bbf60e97…`).
- End to end on Docker Desktop: a session `qualify(..., reviewed=reviewed_reference(), timeout_seconds=60)`
  qualified automatically, and `QualifiedDockerBackend(..., reviewed=...)` graded one request (`completed`,
  `qualified`, correct answers).
- Operational note: under load a probe can hit its Docker command timeout (`PROBE_EXCEPTION`, `not-run`); the
  session then does not qualify. Use `timeout_seconds=60`, run the session without other Docker load, and retry
  with a new `job_id`. Changing the default in code would change `probe_digest` and void the review.
