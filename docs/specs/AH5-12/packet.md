# AH5-12 — Move the agent runner (`runner.py`) onto the library's execution adapters

- Status:
  - **12a accepted** (owner, 2026-10-18). It moves generic code verbatim with no behaviour change.
  - **12b is a separate behaviour-change packet.** The single launch path (one adapter, `AgentCliBackend`) is
    accepted, but that does not accept the eight differences below. Each needs its own acceptance at the master
    review: old and new behaviour, consumer impact, acceptance criterion and basis. 12b is not built until then.
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

## AH5-12b — the eight behaviour changes, each for its own acceptance

Consumers: Showcase is the only caller of `runner.py`. agent-benchmark already uses `AgentCliBackend` directly
and is unaffected by every row.

| # | Old (Showcase `runner.py` at `49ab97f`) | New (runner on `AgentCliBackend`) | Impact on Showcase | Acceptance criterion | Basis | Accepted |
|---|---|---|---|---|---|---|
| 1 | The prompt is an argv element of `codex`/`claude`. | The prompt goes on the CLI's stdin. | The prompt no longer shows in process listings or argv-length limits. No workflow change expected. | A Showcase builder run and a reviewer run each complete with the same task result; a test asserts the prompt is not in argv. | Secrets and untrusted packet text should not be visible to other processes (constitution 11); the library already does this. | pending |
| 2 | Codex flags include `--cd <worktree>`, `--sandbox <mode>`, `approval_policy=never`, `web_search=disabled`, `network_access=false`, `--output-schema`, `--output-last-message`. | One validated option set in the library: worktree, sandbox mode, output schema, last-message file, model, reasoning; network and web search always off. | The same flags reach Codex, in a library-checked form. `--skip-git-repo-check` and `forced_login_method=chatgpt` are added (row 4). | For each Showcase profile, the generated argv equals the old argv apart from the listed additions (a golden test per profile). | One launch path (master rule 7). The library refuses an option that loosens network, web or approval. | pending |
| 3 | Claude: `--restricted --strict-mcp-config --json-schema`, tool sets per profile (reviewers read-only), the denied-Bash list, sandbox settings (`denyRead` of credential paths, no domains), `--max-turns`, `--max-budget-usd`. | The same through validated options: tools, denied commands, `deny_read`, the schema, max turns and budget. The library's minimum (`WebFetch`/`WebSearch` denied, sandbox required) is never loosened. | Same effective permissions. The Claude settings JSON becomes the library's merge of Showcase's settings and its minimum. | A golden argv per profile; a reviewer profile cannot get `Edit`/`Write`; a test shows a profile cannot remove the library minimum. | One launch path; the stricter-wins merge keeps every current denial. | pending |
| 4 | API-key variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) are inherited by the CLI. | They are removed: the CLI runs only on its own login. | **Can refuse runs that work today:** a Showcase host that authenticates only by API key stops working until the CLI is logged in. | Showcase confirms that its hosts and CI run on CLI login, and a run with a key in the environment shows the key absent in the child. | No API billing from harness runs, which is the library's policy since AH5-05b. The provider accounts are the owner's. | pending, **needs Showcase confirmation** |
| 5 | No sandbox probe; a sandbox failure shows only after launch. | The CLI sandbox is probed before launch. If it cannot start, the request is `rejected` with `CAPABILITY_UNSUPPORTED` and a reason; there is no fallback. | **Can refuse runs that work today:** a host whose CLI sandbox does not start no longer runs unsandboxed or half-sandboxed. | A host without a working sandbox gets `rejected` and no agent process starts; a host with one runs as before. | Fail closed (constitution 6): never weaken the sandbox to get a run. | pending, **needs Showcase confirmation** |
| 6 | No timeout and no cancel. | The request's limits apply (wall time, cancel with grace). Without them the library defaults apply. | A run longer than the limit is cancelled. Showcase sets its limits per packet (lease TTL). | Showcase passes its limit; a test shows cancel at the limit leaves no agent process and a `timeout`/`cancel` result. | Bounded execution; the cancel and drain path already exists in the library. | pending |
| 7 | Evidence is `stdout.log`, `stderr.log`, `exit-code.txt` and `provenance.json` in Showcase's run dir, written by the runner. | The library result and evidence root (stdout up to 8 MiB, the usage summary, the contract result) are the source of truth. Showcase writes its provenance from that result. | Run-dir files change in shape. Readers of `stdout.log` and similar (telemetry, orchestrate) must read the result. | Every Showcase reader of those files is listed and updated; orchestrate and telemetry tests pass on the new layout; nothing reads the old files. | One evidence path (master rule 7). | pending |
| 8 | `provider_metadata` parses the CLI's stdout in the runner. | The backend's usage parsing (`usage_completeness`, ADR 0002) is used. | Telemetry fields come from the contract's usage. The old fields map 1:1 or are listed as dropped. | A mapping table from the old to the new fields; Showcase's telemetry tests pass. | One usage rule (ADR 0002). | pending |

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
