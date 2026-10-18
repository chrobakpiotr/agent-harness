# AH5-12 — Move the agent runner (`runner.py`) onto the library's execution adapters

- Status:
  - **12a accepted**: it is in the plan's order, and it moves generic code verbatim.
  - **12b proposed**: it changes how Showcase launches its agents, so the behaviour changes below need the owner's
    and Showcase's acceptance before implementation.
- Source: Showcase `tooling/agent-harness/runner.py` at `49ab97f` (970 lines).
- Rule from the plan: runner moves onto `agent_harness.execution` (`launch`, `AgentCliBackend`) and the verification
  command path. It keeps no second launch/cancel/evidence path for the same operations.

## Findings (2026-10-18)

`runner.py` has three parts:

| Part | Source functions | Library today | Packet |
|---|---|---|---|
| Generic SDD checks | `project_task_result_schema`, `task_result_projection`, `schema_definition_errors`, `canonical_schema_errors`, `packet_hash`, `validate_packet_integrity`, `validate_result`, `git_changed_paths`, `path_allowed`, `worktree_content_fingerprint`, `git_snapshot`, `enforce_postconditions` | none | **12a**, verbatim, with `REPO`/`HERE` schema paths as parameters |
| Verification hook | `run_verification` (runs `verify.py run` under the plan's bound control interpreter in the safe environment) | `verify_cli`, `sandbox._safe_env`, `executables` | **12a**. The `verify.py` path and profile root become parameters |
| Provider launch | `codex_command`, `claude_command`, `claude_sandbox_settings`, `claude_schema_text`, `extract_claude_*`, `provider_metadata`, `cli_version`, `preview_command`, `main`'s `subprocess.run(cmd)` | `AgentCliBackend` (claude, codex, opencode) with `launch` | **12b**, onto `AgentCliBackend` |
| Showcase policy | `role_file`, `render_prompt` (role prompts, feedback trust), `TASK_RESULT_PROJECTION_PROFILES`, `verification_argv` (a duplicate of `verification_command`'s allowlist) | — | stays in Showcase. The duplicate allowlist folds into `verification_command` |

## AH5-12b — differences that must be decided (each is a behaviour change for Showcase)

| # | Showcase runner today | Library `AgentCliBackend` today | Proposal |
|---|---|---|---|
| 1 | The prompt is an argv element. | The prompt goes on stdin. | stdin. The prompt is no longer visible in the process list. |
| 2 | Codex: `exec --ephemeral --json --cd <worktree> --sandbox <mode> -c approval_policy=never -c web_search=disabled -c network_access=false --output-schema <schema> --output-last-message <file>`. | Codex: `exec -m <model> --sandbox <fixed> -c forced_login_method=chatgpt --skip-git-repo-check --json --ephemeral -`. | The library takes a validated option set: `output_schema`, `read_only`, `model`, `reasoning`, `network=false`, `web_search=false`. The union of both flag sets, with the stricter value where they conflict. |
| 3 | Claude: `--restricted --strict-mcp-config --json-schema`, tools by profile (read-only for reviewers), denied Bash patterns, sandbox settings (`denyRead` of credential paths, no domains), `--max-turns`, `--max-budget-usd`. | Claude: `acceptEdits`, the library's own settings, `--disallowedTools WebFetch,WebSearch`. | The same option set, plus `tools`, `denied_commands`, `deny_read`, `max_turns` and `max_budget_usd`. The library validates them and never loosens its own minimum. |
| 4 | The API-key variables are inherited. | They are removed: a run never bills an API account. | Removed. Showcase must run on CLI login. |
| 5 | No sandbox probe; a failure shows only after launch. | The sandbox is probed before launch; if it cannot start, the result is `rejected`/`CAPABILITY_UNSUPPORTED`. | The probe applies. A run that would have started unsandboxed is now refused. |
| 6 | No timeout or cancel. | Cancel, grace and the timeout limits come from the request. | Showcase passes its limits. Without them the library defaults apply. |
| 7 | `stdout.log`, `stderr.log`, `exit-code.txt` and `provenance.json` in Showcase's run dir. | Stdout up to 8 MiB in the evidence root; a usage summary; a contract result. | The library result and evidence are the source of truth. Showcase writes its provenance from the result, so there is one evidence path. |
| 8 | `provider_metadata` parsing in the runner. | Usage parsing in the backend (`usage_completeness`). | The library's. |

The decision is needed on rows 1–8, especially 4 and 5, which can refuse runs that work today. Until 12b is
accepted, `runner.py`'s launch stays as it is in Showcase. 12a does not change it.

## AH5-12a — acceptance criteria

- The generic checks and the verification hook move verbatim, with explicit schema, verify-CLI and profile-root
  parameters.
- The ported tests pass, and each seam has a test and a mutation check.
- Showcase's `test_runner` passes through the shim, and the rehearsal at `49ab97f` gives the AH5-11 results.
- Source and test map, behaviour changes (none intended) and consumer update, as in AH5-11.
