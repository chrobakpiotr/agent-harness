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
| 4 | API-key and token variables (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY`, Bedrock/Vertex switches, …) are inherited unchanged; Showcase never sets them itself, so whatever is in the caller's environment decides authentication. | Library default: the variables in `execution._BILLING_ENV` are removed, and the CLI runs only on its own login. | **Lost scenarios:** (a) a host whose CLI has no login and authenticates only through a key in the environment; (b) a headless container or CI job where interactive login is not possible and a key is the provisioned credential (Showcase's CI does not launch providers today; it only compiles `runner.py`); (c) runs that are meant to bill an API, Bedrock or Vertex account. **Hidden today:** when a key and a login are both present, the Claude CLI may use the key, so a run can bill an API account unnoticed. | Showcase states which of (a)–(c) it uses. Whichever option is chosen, a test shows the credential source of each run is explicit and recorded in the run's provenance, and that no run silently bills an API account. | The goal of one launch path does not by itself justify removing a working authentication method. **Options for decision:** (i) strip, as the library does now; (ii) strip by default, with an explicit consumer opt-in (`credential_mode='environment'`) that passes the variables through and records it per run; (iii) keep inheriting. Harness recommends (ii): no surprise billing, and nothing that works today is lost. | **Showcase position (2026-10-19): accept option (i), strip.** No pass-through and no API-key fallback; an unauthenticated CLI fails closed. Basis: Showcase configures no API-key agent runs; the accepted RUNNER-STRICT-OUTPUT-001 spec uses Claude's OAuth session in restricted mode (`runner.py:415`); its CI launches no provider CLI. Added requirement: a migration note telling users to authenticate through the provider CLI first. Owner decision pending. |
| 5 | Claude: Showcase's settings already require the sandbox (`failIfUnavailable: true`, `allowUnsandboxedCommands: false`), so a host without it refuses when a sandboxed command runs. Codex: Showcase passes `--sandbox read-only` for reviewer and review-existing runs and `workspace-write` for builders, and Codex enforces it. No pre-launch probe. | A probe runs before launch: `codex sandbox -c sandbox_mode=… -- true`; on Linux, Claude checks for `bwrap` and `socat`; on macOS, Claude's probe is only `claude --version`. If the probe fails, the request is `rejected`/`CAPABILITY_UNSUPPORTED` before any agent process starts. The library's Codex sandbox mode is currently fixed at `workspace-write`. | **Lost scenarios:** (a) a host where the probe fails but a run would have done useful work: an agent that only reads and answers, without sandboxed commands, on a host without a working Codex or Claude sandbox (nested containers, Linux without the needed kernel support); today it runs, with 12b it is refused; (b) **an isolation regression unless kept:** reviewer runs would move from `read-only` to the fixed `workspace-write`. Row 2 must keep the per-run sandbox mode, and with it the reviewer `read-only` mode. The Claude refusal itself is not new; only its timing changes (before launch rather than at the first command). | A golden test shows reviewer runs stay `read-only`. On each of Showcase's actual hosts, the probe result is recorded and matches what a real sandboxed command does, with no host refused that runs today unless Showcase accepts that loss. | Fail closed is already Showcase's Claude policy. **Options for decision:** (i) probe before launch, as the library does now; (ii) keep the CLI's own enforcement without a pre-launch probe, the probe being diagnostic only; (iii) probe plus the per-run sandbox mode. Harness recommends (iii) once Showcase's hosts are checked. The sandbox mode is a precondition in every option. | **Showcase position (2026-10-19): accept the pre-launch probe.** It never falls back to unsandboxed execution. Added criterion: a blocked preflight reports a clear blocked outcome **without consuming or starting the task attempt**. The explicit per-run sandbox mode (reviewer `read-only`) stays a precondition, through row 2. Owner decision pending; the host check is still to be recorded. |
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
- Current consumer (2026-10-19). The rehearsal ran against Showcase `cbbdbca`, which is already cut over and pinned to
  v0.12.0; the base side ran that pin (`BASE_PINNED=1`). The host was quiet, with load about 3–8, recorded in the
  summary.
  - Rollback is ok in all three directions.
  - Wrapped-only differences:
    - the three `test_qualification_report` version-pin tests, which move with the re-pin;
    - Showcase's own `test_cutover_wrapper` eval test, overwritten by the copy and now merged into the library's
      file;
    - `test_verification_parity.test_runner_forwards_only_exact_accepted_plan_to_shared_cli`, a real AH5-12a
      regression. The port imported `load_profile` at module level, while the source looks it up per call, which
      Showcase's test patches. Fixed in `task_runner.run_verification`.
  - With the fix installed, `test_verification_parity`, `test_runner` and the adversarial eval (7/7) pass on the
    current consumer.
- AC3, independent evaluation of 0.13.0 (AH5-11 + AH5-12a only):
  - **Attempt 1, no verdict.** The evaluator stopped on an API session limit (2026-10-18) before reporting; it is
    kept as an attempt, not a result.
  - **Attempt 2** reviews branch `ah5-12a-runner-checks` at `daecf4e`. Its verdict will name exactly the sources
    it evaluated.
