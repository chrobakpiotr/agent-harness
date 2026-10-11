# Using agent-harness: running agents and grading on a qualified target

One page for consumers: what each part does, how to call it, and what its results mean. Decisions and evidence live
in the ADRs and packets linked at the end. Pin a release by its commit SHA (see the README).

## 1. The launch API

`execution.launch(request, backend, workspace=..., evidence_root=...)` validates a contract request, runs it on a
backend and returns an `Execution`. `execution.result(timeout)` gives the validated contract result; `cancel()` stops
it. Results are published once per `request_id` under `evidence_root/executions/`: relaunching an ID returns the stored
result, and a different request under the same ID raises `BINDING_MISMATCH`. Execution `outcome`, harness
`completion` and your grade are separate facts.

| Backend | Isolation | Use |
|---|---|---|
| `ScriptedBackend` | `fake` | deterministic tests |
| `ProcessBackend(argv)` | `controlled` | a local process group, no sandbox |
| `AgentCliBackend(provider, prompt, …)` | `controlled` | a coding-agent CLI on its subscription login |
| `QualifiedDockerBackend(…)` | `qualified` | grading on the qualified Docker target |

## 2. Running a coding agent: `AgentCliBackend`

```python
from agent_harness import contract, execution

backend = execution.AgentCliBackend("claude", prompt, diff_base=base_commit)  # or "codex"
result = execution.launch(request, backend, workspace=ws, evidence_root=evidence).result()
```

- The request's `provider` must be `claude`, `codex` or `opencode`, and its `model` is passed to the CLI.
- **Fixed command lines.** The prompt goes in on stdin, so it can never act as a flag; there are no caller arguments.
- **Credentials.** API keys and routing variables are removed from the CLI's environment, and the login method is
  pinned to the subscription. No spend.
- **Sandboxes.**
  - Claude Code's Bash sandbox runs with no network and fails closed; login directories are unreadable; sandboxed
    Bash is allowed outright, so test commands run.
  - Codex runs with `--sandbox workspace-write`.
  - If a sandbox probe fails, the request is `rejected` / `CAPABILITY_UNSUPPORTED`, with the reason in
    `backend.rejection_reasons[request_id]`. There is no fallback mode.
- **Evidence root.** It must be outside the workspace, `/tmp` and `$TMPDIR` (the sandboxes can write there),
  otherwise the request is refused.
- **Result.**
  - `usage_events` hold the CLI's own token report; the raw output is the `agent-output` artifact.
  - The candidate is one workspace file (`candidate=`) or `git diff --binary <diff_base>` (`diff_base=`), built from
    a Git directory Harness owns.
  - A run that is not `completed` has no candidate. Outputs carrying login credentials, in plain, base64 or hex form,
    are withheld.
- **Provider errors.** They are `error` / `PROVIDER_ERROR`, and `backend.error_details[request_id]` says
  `quota_exhausted` (with `resets_at` when the CLI states it) or `provider_error`.
- **Real CLIs.** Check them before a run: `PYTHONPATH=src python3 scripts/smoke-agent-cli.py`.
- **OpenCode on OpenRouter (macOS).** Call `AgentCliBackend("opencode", prompt, api_key_file="~/…/openrouter.key")`
  with a request model of the form `vendor/model:free`.
  - OpenCode runs under a Seatbelt profile whose only network is a relay on 127.0.0.1. The relay holds the key and
    forwards only that model.
  - Pass the key as a mode-600 file, so the sandbox can deny reading it.
  - When the daily free cap is hit, the result is `quota_exhausted` with `resets_at`. Nothing more is forwarded.
  - Details and limits: packet `AH5-05b`.

## 3. Qualifying a grading session: `qualification.qualify`

A target is qualified per job (ADR 0002). A grading session qualifies by matching the independently reviewed
reference shipped in the package. That reference fixes the target tuple, the probe code (`probe_digest`), the policy
(`policy_digest`) and the grading layout.

```python
from agent_harness import qualification
from agent_harness.qualification.reference import reviewed_reference

reviewed = reviewed_reference()  # Docker Desktop tuple of this release
session = qualification.qualify(job_id, out_dir, reviewed=reviewed, timeout_seconds=60)
assert session["qualified"]  # else retry with a new job_id on a quiet machine
```

- It runs Q01–Q16 and B1–B10 plus the grading-layout probe, re-running checks that only hit a Docker timeout.
- `out_dir` must be new and empty.
- Record `session["qualification_digest"]`, `["reviewed_qualification_digest"]` and `["probe_digest"]` with each
  grade (B10).
- `target_kind` is `docker-desktop` (default), `github-runner` or `linux-docker`. A tuple without a shipped reference
  needs a new qualification run and an independent review first.

## 4. Grading: `QualifiedDockerBackend`

```python
backend = execution.QualifiedDockerBackend(
    session["report_doc"], qualification_evidence=out_dir / "evidence",
    capability_report=session["capability_doc"], job_id=job_id, image=qualification.DEFAULT_IMAGE,
    command=["python3", "-c", DRIVER, "/inputs/cases.json", "/output/answers.json"],
    cases=cases_json, reviewed=reviewed)
```

- One backend is one session and grades many contract-v2 requests with `limits = execution.QUALIFIED_LIMITS`.
- Each request gets a fresh container with exactly the qualified flags:
  - the workspace (with the candidate already applied) is streamed into a tmpfs;
  - `cases.json` is read-only;
  - the environment is the image's plus `GRADING_ENV`.
- `answers.json` comes back as the `answers` artifact, read only after the container is confirmed gone. The verdict
  is yours; expected values never enter the container.
- `limits.fired` is `timeout`, `oom` or `disk` (more than 1 MiB in `/output`).
- A request is refused before launch if its limits differ or the live Docker tuple drifts from the qualified one.

## References

ADR 0002 (contract, qualification, review-per-tuple amendment), ADR 0004 (launch API), ADR 0005 (contract v2);
packets `docs/specs/AH5-05a`, `AH5-05b`, `AH5-04c`.
