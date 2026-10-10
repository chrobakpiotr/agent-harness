# AH5-04c — Grading on the qualified target (`QualifiedDockerBackend`)

- Status: **draft for consumer review** (agent-benchmark). Nothing implemented.
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

- **D1 — qualification per job (blocking).** ADR 0002: "nothing stays qualified between jobs";
  `validate_capability_binding` requires the caller's `job_id`. The accepted 04b report qualifies only its own
  local job.
  - (a) **Recommended:** every grading session runs Showcase's qualification (26 checks, a few minutes on Docker
    Desktop) under a new session `job_id` first, and grades in that job. This is strict and needs no ADR change.
    It requires the Showcase qualification runner to be callable by the benchmark (Showcase-owned script).
  - (b) Amend ADR 0002 so a persistent local target stays qualified while the live tuple (engine, kernel, image,
    policy digest) is unchanged. Faster, but weaker: host changes that do not alter the tuple go unnoticed.
- **D2 — fired limits that the host cannot observe.** PIDs and disk exhaustion happen inside the container
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
