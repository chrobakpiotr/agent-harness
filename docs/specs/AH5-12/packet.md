# AH5-12 — Move the agent runner (`runner.py`) onto the library's execution adapters

- Status: **accepted** (owner, 2026-10-18).
  - 12a moves generic code verbatim.
  - 12b moves the provider launch onto `AgentCliBackend` with the proposals in its table, behaviour changes
    included. It follows the master plan's rule: one execution path, every change explicit.
  - The packets go one at a time: 12a first, then 12b.
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

Rows 1–8 are accepted as proposed. Rows 4 and 5 can refuse runs that work today: an API key no longer reaches
the CLI, and an unsandboxed start is refused. Showcase's consumer update for 12b must state both. 12a does not
change the launch.

## AH5-12a — acceptance criteria

- The generic checks and the verification hook move verbatim, with explicit schema, verify-CLI and profile-root
  parameters.
- The ported tests pass, and each seam has a test and a mutation check.
- Showcase's `test_runner` passes through the shim, and the rehearsal at `49ab97f` gives the AH5-11 results.
- Source and test map, behaviour changes (none intended) and consumer update, as in AH5-11.

## AH5-12a — source and test map, changes, consumer update, evidence (2026-10-18)

| Showcase (`49ab97f`) | Library | Notes |
|---|---|---|
| `runner.py` (`9b3a7177`), the checks listed in the findings | `agent_harness/task_runner.py` | Verbatim apart from the seams in `provenance.md` |
| `runner.py`, everything else | `migration/showcase-wrapper/runner.py` | Showcase's file with the checks bound to the library; the launch is unchanged |
| `tests/test_runner.py` (`8678338c`, 27) | `tests/test_task_runner.py` (16 + 2) | The 11 launch tests stay until 12b |

- **Behaviour changes:** none for Showcase. In the library, the checks have no built-in projection profiles, schema,
  verification CLI or profile root.
- **Consumer update (Showcase):** at the re-pin, copy `migration/showcase-wrapper/runner.py` over
  `tooling/agent-harness/runner.py`. No test removals. From the freeze notice, the moved checks are frozen in it;
  the launch stays Showcase-owned until 12b.
- **Evidence:**
  - `test_task_runner`: 18 OK.
  - Mutating the binding check, the consumer CLI or the profile root fails the suite.
  - Showcase's `test_runner`: 27/27 on both sides (scratch copies of `49ab97f`).
  - The rehearsal and evaluation are recorded with the release.
