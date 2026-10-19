# AH5-12b — the agent-launch adapter interface for consumers (draft, unreleased)

- Status: **interface draft**, written against branch `ah5-12b-agent-launch`. It is not released. It ships with the
  12b release and its compatibility note. Decisions: `docs/specs/AH5-12/packet.md` (rows 1–11, D1–D6).
- `AgentCliBackend` exists in released versions (v0.12.0 included). 12b adds `cli_options`, the isolation check,
  `launch.json` and `agent-stderr`. Without `cli_options` the command line is what it was, apart from Claude's
  settings, which now also deny the file tools the evidence root. New for every run: the isolation check before
  launch, `launch.json`, `agent-stderr`, and withheld output keeping its real outcome (`withheld.json`).

## Launch

```python
from agent_harness import contract, execution

backend = execution.AgentCliBackend(provider, prompt, env=os.environ, cli_options=options)
request = contract.validate_request({
    "contract_version": 1, "request_id": invocation_id, "trial_id": None,
    "task_digest": "sha256:" + packet_sha256, "config_digest": "sha256:" + sha256(canonical(options + model)),
    "provider": provider, "model": model,            # D1: resolved before launch, never the CLI default
    "settings": {}, "capabilities": ["usage", "cancel"],
    "timeout_seconds": agent_timeout,                # D2: --agent-timeout, Showcase default 1800 s
    "max_attempts": 1, "input_bindings": []})
result = execution.launch(request, backend, workspace=str(worktree),
                          evidence_root=str(run_dir / "evidence")).result()
```

- `provider` is `claude` or `codex`.
- **`env`:** pass the real environment. The library removes the API-key and token variables (row 4) and records
  that in `launch.json`.
- **`prompt`:** the full rendered prompt. It goes on stdin as UTF-8 (row 1).
- **`workspace`:** the isolated task worktree.
- **`evidence_root`:** must not be writable by the agent's sandbox (D5). A primary checkout as the worktree is
  refused when this cannot be assured.
- **Do not pass `sandbox_probe` or `isolation_probe` in production.** The defaults run the real availability and
  isolation checks (row 5). A failure gives `outcome: rejected` / `CAPABILITY_UNSUPPORTED` before any agent process
  or attempt starts; `backend.rejection_reason` says why.

## `cli_options` (validated; anything that would loosen the library minimum raises `ValueError`)

| Option | codex | claude | Showcase builder | Showcase reviewer / review-existing |
|---|---|---|---|---|
| `sandbox_mode` | `workspace-write` or `read-only` | the same (read-only: `Edit`/`Write`/`NotebookEdit` denied, workspace in `denyWrite` for sandboxed Bash) | `workspace-write` | `read-only` |
| `output_schema` | `--output-schema`, plus the `last-message` artifact | `--json-schema` | the task-result projection (12a `task_result_projection`) | the same |
| `strict` | `approval_policy=never`, `web_search=disabled`, network off | — | `True` | `True` |
| `reasoning` | `model_reasoning_effort` | — | `--reasoning` | `--reasoning` |
| `login_method` | `chatgpt` only (the library default, passed explicitly) | — | `chatgpt` | `chatgpt` |
| `restricted` | — | `--restricted --strict-mcp-config --no-session-persistence` | `True` | `True` |
| `permission_mode` | — | `dontAsk` only | `dontAsk` | `dontAsk` |
| `tools` | — | `--tools`, plus `--allowedTools` without Bash | `Read,Edit,Write,Glob,Grep,Bash` | `Read,Glob,Grep,Bash` |
| `denied_commands` | — | added to `--disallowedTools` (`WebFetch`/`WebSearch` always denied) | Showcase's `Bash(git push *)` … list | the same |
| `deny_read` | — | added to the sandbox `denyRead` (login paths always denied) | Showcase's credential paths | the same |
| `max_turns`, `max_budget_usd` | — | `--max-turns`, `--max-budget-usd` | `--max-turns`, `--max-budget-usd` | the same |

The runner's own repository and worktree validation stays (row 2): `--skip-git-repo-check` does not replace it.

## Result and evidence (rows 7–11)

- **The contract result** (`outcome`, `exit_code`, `error_code`, `drain`, `resolved_model`, `usage_events`,
  `usage_completeness`) is the process outcome. `drain: unconfirmed` means the end of the agent's child processes
  could not be confirmed (row 6).
- **Artifacts** under `evidence_root/executions/<slot>/`:
  - `agent-output`: stdout; Claude's JSON envelope or Codex's JSONL;
  - `last-message`: Codex with `output_schema`;
  - `agent-stderr`: D3, size-bounded and secret-checked;
  - `launch.json`: the argv without the prompt, the requested login mode, `billing_source_confirmed: false`, and
    Claude's effective permissions.
- **Task-result extraction stays in Showcase** (row 9). Codex reads the `last-message` artifact; Claude reads
  `structured_output` from `agent-output`, and an unstructured reply is still refused. With no artifact there is no
  task result.
- **`provider_metadata`** comes from the same `agent_harness.telemetry` parsers applied to `agent-output`; its
  semantics are unchanged (row 8). `cost_usd` is taken only from a provider-reported value.
- **Compatibility readers:**
  - Showcase keeps writing `provenance.json`, the single record `orchestrate` and `telemetry` read, built from the
    result.
  - `stdout.log`/`stderr.log` are compatible *reads* of the `agent-output`/`agent-stderr` artifacts, not a second
    write (D3).
  - Legacy run directories are read as they are and never rewritten (row 7).
- **Withheld output** (credential-shaped, or over 8 MiB) gives no `agent-output` and no candidate. The process facts
  stay as they were (`outcome`, `exit_code`, `drain`); an artifact `withheld` (`withheld.json`) says why:

  ```json
  {"schema_version": 1, "reason_code": "OUTPUT_LIMIT_EXCEEDED | CREDENTIAL_SHAPED_OUTPUT",
   "artifacts": ["agent-output", "last-message"], "size": 9000000, "limit": 8388608, "size_is_lower_bound": false,
   "details": [{"artifact": "agent-output", "reason_code": "...", "size": 9000000, "limit": 8388608,
                "size_is_lower_bound": false}],
   "secret_detected": false, "note": "process outcome, artifact publication and task-result acceptance are separate facts"}
  ```

  `size`/`limit` are null for a credential-shaped artifact. `details[].reason_code` can also be
  `CANDIDATE_UNSAFE_OBJECT`, `CANDIDATE_UNAVAILABLE`, or `WITHHELD_WITH_OUTPUT` (dropped because the main output was
  withheld). A withheld `agent-stderr` alone keeps the output and the candidate.

## Showcase's runner adapter (`migration/showcase-wrapper/runner.py`)

- **`--preflight`**: resolves the model, builds the request and runs `backend.refuse()` (model, CLI, sandbox,
  isolation, evidence protection). It prints `preflight: ok` (exit 0) or `preflight refused: <reason>` (non-zero).
  It launches nothing and writes nothing. `orchestrate` calls it **before** the lifecycle starts the attempt, so a
  refusal never uses an attempt (row 5). A refusal at launch time is still recorded (`status: preflight-refused`).
- **D1:** `--model`, else `DEFAULT_MODELS[provider]` (empty: refused before anything is written). Provenance has
  `model_requested`, `model_source` (`argument` | `configuration`) and `model_resolved` (the provider's report).
- **D2:** `--agent-timeout`, default 1800 s, 1..86400. Orchestrate's lease heartbeat runs until the runner exits,
  so the lease covers execution, cancel, drain and the result write.
- **Provenance:** the same `provenance.json` with the same fields, plus `agent_execution` (the process outcome,
  exit code, error code, drain, usage completeness, published artifacts relative to the run dir, `withheld_reason`).
  `provider_metadata` comes from the same parsers applied to the published `agent-output`; with no published
  output it is `{"usage": null, "cost_usd": null}`.
- **Statuses:** `preflight-refused`; `output-withheld` (not a provider error); `provider-error` (non-zero exit,
  timeout, cancel, launch error or unknown).
- **Logs:** `run_output(run_dir, 'stdout' | 'stderr')` reads an old run's `stdout.log`/`stderr.log` as they are, and
  a new run's library artifacts. Nothing writes a second copy; `provider-envelope.json` is no longer written.
- **Not moved yet:** `codex_command`/`claude_command` stay, for `--print-command` and for the launches in
  `design.py`, `wayfinder.py` and `verification_contract.py`. Those move with their own packets (AH5-13, AH5-16).
