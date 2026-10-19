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

Consumers: Showcase is the only caller of `runner.py`, but agent-benchmark uses the same `AgentCliBackend`
(`harness_port.py`). Every change to its options, defaults or results needs a compatibility check for benchmark.
Showcase-only policy is passed through Showcase's adapter; it does not become a library default.

| # | Old (Showcase `runner.py` at `49ab97f`) | New (runner on `AgentCliBackend`) | Impact on Showcase | Acceptance criterion | Basis | Accepted |
|---|---|---|---|---|---|---|
| 1 | The prompt is an argv element of `codex`/`claude`. | The prompt goes on the CLI's stdin. | The prompt no longer shows in process listings or argv-length limits. No workflow change expected. | A Showcase builder run and a reviewer run each complete with the same task result; a test asserts the prompt is not in argv. | Secrets and untrusted packet text should not be visible to other processes (constitution 11); the library already does this. | pending |
| 2 | Codex: `exec --ephemeral --json`, `--cd <worktree>`, `--sandbox <mode>`, `approval_policy=never`, `web_search=disabled`, `network_access=false`, `--output-schema`, `--output-last-message`. | One validated option set in the library: worktree, sandbox mode, output schema, last-message file, model, reasoning; network and web search always off. | The same flags reach Codex, in a library-checked form. `--skip-git-repo-check` and `forced_login_method=chatgpt` are added (row 4). | For each Showcase profile, the generated argv equals the old argv apart from the listed additions (a golden test per profile). | One launch path (master rule 7). The library refuses an option that loosens network, web or approval. | pending |
| 3 | Claude: `--restricted --strict-mcp-config --json-schema`, tool sets per profile (reviewers read-only), the denied-Bash list, sandbox settings (`denyRead` of credential paths, no domains), `--max-turns`, `--max-budget-usd`. | The same through validated options: tools, denied commands, `deny_read`, the schema, max turns and budget. The library's minimum (`WebFetch`/`WebSearch` denied, sandbox required) is never loosened. | Same effective permissions. The Claude settings JSON becomes the library's merge of Showcase's settings and its minimum. | A golden argv per profile; a reviewer profile cannot get `Edit`/`Write`; a test shows a profile cannot remove the library minimum. | One launch path; the stricter-wins merge keeps every current denial. | pending |
| 4 | API-key and token variables (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY`, Bedrock/Vertex switches, …) are inherited unchanged; Showcase never sets them itself, so whatever is in the caller's environment decides authentication. | Library default: the variables in `execution._BILLING_ENV` are removed, and the CLI runs only on its own login. | **Lost scenarios:** (a) a host whose CLI has no login and authenticates only through a key in the environment; (b) a headless container or CI job where interactive login is not possible and a key is the provisioned credential (Showcase's CI does not launch providers today; it only compiles `runner.py`); (c) runs that are meant to bill an API, Bedrock or Vertex account. **Hidden today:** when a key and a login are both present, the Claude CLI may use the key, so a run can bill an API account unnoticed. | Showcase states which of (a)–(c) it uses. Whichever option is chosen, a test shows the credential source of each run is explicit and recorded in the run's provenance, and that no run silently bills an API account. | The goal of one launch path does not by itself justify removing a working authentication method. **Options for decision:** (i) strip, as the library does now; (ii) strip by default, with an explicit consumer opt-in (`credential_mode='environment'`) that passes the variables through and records it per run; (iii) keep inheriting. Harness recommends (ii): no surprise billing, and nothing that works today is lost. | **Showcase position (2026-10-19): accept option (i), strip.** No pass-through and no API-key fallback; an unauthenticated CLI fails closed. Basis: Showcase configures no API-key agent runs; the accepted RUNNER-STRICT-OUTPUT-001 spec uses Claude's OAuth session in restricted mode (`runner.py:415`); its CI launches no provider CLI. Added requirement: a migration note telling users to authenticate through the provider CLI first. Owner decision pending. |
| 5 | Claude: Showcase's settings already require the sandbox (`failIfUnavailable: true`, `allowUnsandboxedCommands: false`), so a host without it refuses when a sandboxed command runs. Codex: Showcase passes `--sandbox read-only` for reviewer and review-existing runs and `workspace-write` for builders, and Codex enforces it. No pre-launch probe. | A probe runs before launch: `codex sandbox -c sandbox_mode=… -- true`; on Linux, Claude checks for `bwrap` and `socat`; on macOS, Claude's probe is only `claude --version`. If the probe fails, the request is `rejected`/`CAPABILITY_UNSUPPORTED` before any agent process starts. The library's Codex sandbox mode is currently fixed at `workspace-write`. | **Lost scenarios:** (a) a host where the probe fails but a run would have done useful work: an agent that only reads and answers, without sandboxed commands, on a host without a working Codex or Claude sandbox (nested containers, Linux without the needed kernel support); today it runs, with 12b it is refused; (b) **an isolation regression unless kept:** reviewer runs would move from `read-only` to the fixed `workspace-write`. Row 2 must keep the per-run sandbox mode, and with it the reviewer `read-only` mode. The Claude refusal itself is not new; only its timing changes (before launch rather than at the first command). | A golden test shows reviewer runs stay `read-only`. On each of Showcase's actual hosts, the probe result is recorded and matches what a real sandboxed command does, with no host refused that runs today unless Showcase accepts that loss. | Fail closed is already Showcase's Claude policy. **Options for decision:** (i) probe before launch, as the library does now; (ii) keep the CLI's own enforcement without a pre-launch probe, the probe being diagnostic only; (iii) probe plus the per-run sandbox mode. Harness recommends (iii) once Showcase's hosts are checked. The sandbox mode is a precondition in every option. | **Showcase position (2026-10-19): accept the pre-launch probe.** It never falls back to unsandboxed execution. Added criterion: a blocked preflight reports a clear blocked outcome **without consuming or starting the task attempt**. The explicit per-run sandbox mode (reviewer `read-only`) stays a precondition, through row 2. Owner decision pending; the host check is still to be recorded. |
| 6 | No timeout and no cancel. | The request's limits apply (wall time, cancel with grace). Without them the library defaults apply. | A run longer than the limit is cancelled. Showcase sets its limits per packet (lease TTL). | Showcase passes its limit; a test shows cancel at the limit leaves no agent process and a `timeout`/`cancel` result. | Bounded execution; the cancel and drain path already exists in the library. | pending |
| 7 | The runner writes Showcase's run dir: `prompt.txt`, `packet.json`, `task-result-output-schema.json`, `stdout.log`, `stderr.log`, `exit-code.txt`, `provenance.json`, `provider-envelope.json` (Claude), and the result file, rewritten from the structured output. | The library result and evidence root (stdout up to 8 MiB, the usage summary, the contract result) are the source of truth. Showcase writes its provenance from that result. | Run-dir files change in shape. Readers of `stdout.log` and similar (telemetry, orchestrate) must read the result. | Every Showcase reader of those files is listed and updated; orchestrate and telemetry tests pass on the new layout; nothing reads the old files. | One evidence path (master rule 7). | pending |
| 8 | `provider_metadata` parses the CLI's stdout in the runner. | The backend's usage parsing (`usage_completeness`, ADR 0002) is used. | Telemetry fields come from the contract's usage. The old fields map 1:1 or are listed as dropped. | A mapping table from the old to the new fields; Showcase's telemetry tests pass. | One usage rule (ADR 0002). | pending |
| 9 | Result extraction. Codex: the runner reads its `--output-last-message` file. Claude: it reads `structured_output` from the JSON envelope and refuses an unstructured fallback (`extract_claude_result`); `provider-envelope.json` is written. | **Unchanged in meaning; only the source of the bytes moves.** Codex: the backend's `last-message` artifact. Claude: the backend's `agent-output`. The runner keeps `extract_claude_result`'s refusal of unstructured output, and an output withheld for size or leaks gives no result. | None intended. | Showcase's extraction tests pass on the backend's artifacts; a truncated or withheld output gives no result; an unstructured Claude reply is still refused. | One evidence path; the behaviour is kept. | listed for completeness; covered by the accepted rows 1, 7 and 8 |
| 10 | The `cli_version` probe (5 s timeout) picks the exact task-result projection profile; `--print-command` (`preview_command`, `RUNS/schema-previews`) renders a launch without a CLI. | **Unchanged.** It stays in Showcase's runner as consumer policy, and its projection uses `task_runner` (12a). `--print-command` renders the backend's argv (`launch.json`) instead of a hand-built one. | The preview shows the argv that will actually run. | With and without the CLI installed, the preview and the selected profile match today's for each provider; an unknown CLI version still refuses. | No second launch path; the preview shows the real argv. | listed for completeness |
| 11 | A non-zero provider exit writes `provider-error` to provenance and stops the runner (`die`). | **Unchanged in meaning.** A backend `error`/`PROVIDER_ERROR` or `timeout` result, including a quota with `resets_at`, leads to the same provenance status and stop. Quota exhaustion is now named explicitly (`error_details`). | Clearer diagnosis; same stop. | For each provider error class, a test shows the same runner exit and provenance status as today. | Behaviour is kept. | listed for completeness |

### Decisions (master review, 2026-10-19)

Rows 9–11 were added after the 0.13.0 evaluation found the table incomplete. They record behaviours that stay
the same in meaning, each with an acceptance criterion; no new behaviour change.

All eight rows are accepted with the conditions below; no further table approval is needed. 12b is implemented and
evaluated separately from 0.13.0 (AH5-11 + AH5-12a).

| # | Decision | Conditions that become acceptance criteria |
|---|---|---|
| 1 | accept | The full prompt and its encoding are preserved on stdin. A prompt missing from argv does not mean it is missing from logs: the evidence and logs are checked as well. |
| 2 | accept, corrected | Per-profile sandbox modes are kept, especially reviewer `read-only`. `forced_login_method=chatgpt` is Showcase policy (see the discrepancy below). `--skip-git-repo-check` never replaces the runner's own check that it is in the right repository and worktree. |
| 3 | accept | Effective permissions are checked after the settings merge, not only the argv. A reviewer cannot write through Bash either. Network, MCP and credential-path restrictions are kept. |
| 4 | accept *strip* for Showcase, no fallback | The authentication mode is recorded per run, with no secrets. The actual billing source is not claimed unless the provider confirms it. Benchmark's policy is unchanged. The migration note says: authenticate through the provider CLI first. |
| 5 | accept, with a condition | `claude --version` proves only that the CLI is available, and `bwrap`/`socat` being present proves only that the tools exist. Tool availability is checked separately from an isolation check that actually exercises the sandbox. Runtime enforcement is kept; there is no fallback; a refusal does not consume or start the task attempt. |
| 6 | accept | The execution time limit and the lease TTL stay distinct. There is time for cancel, drain and writing the result. If the end of descendant processes cannot be confirmed, the result reports uncertainty, not a successful cleanup. |
| 7 | accept, corrected for compatibility | New runs have one canonical record. Old run directories stay readable through an explicit read adapter, and historical evidence is never rewritten. Truncated output is marked, and a structured result reconstructed from incomplete output is not accepted. |
| 8 | accept | A field mapping. Unknown stays unknown, never zero. Completeness is explicit. Historical telemetry stays readable. |

**Codex login method (row 2/4), decided by master on 2026-10-19:** `forced_login_method="chatgpt"` stays the
library's current default for the Codex backend, and Showcase's adapter also passes it explicitly. Conditions, as
acceptance criteria:
- Benchmark keeps its current behaviour without any new configuration.
- Without a suitable login session the run is refused. There is no fallback to an API key.
- The compatibility check includes an environment that has an API key alongside a login session.
- Provenance records the *requested* authentication mode. It is never presented as the provider's confirmation of
  how the run was billed.
- The decision covers only this Codex parameter: no other provider and no new option.

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
| `tests/test_runner.py` (`8678338c`, 27) | `tests/test_task_runner.py` (16 ported + 4 added) | Of the other 11, 8 launch tests move with 12b. 3 `render_prompt`/context-trust tests are Showcase prompt policy and stay. |

- **Behaviour changes:** none for Showcase. In the library, the checks have no built-in projection profiles, schema,
  verification CLI or profile root.
- **Consumer update (Showcase):** at the re-pin, copy `migration/showcase-wrapper/runner.py` over
  `tooling/agent-harness/runner.py`. No test removals. From the freeze notice, the moved checks are frozen in it;
  the launch stays Showcase-owned until 12b.
- **Evidence:**
  - `test_task_runner`: 20 OK.
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
  - **Attempt 2 verdict: pass with amendments.** It evaluated branch `ah5-12a-runner-checks` at `daecf4e`, against
    Showcase `49ab97f` (verbatim checks) and `cbbdbca` (parity). Applied after it, before tagging:
    1. A library guard against the `load_profile` regression (`test_profile_is_loaded_per_call_so_consumer_patches_reach_it`), and a runner binding test in `test_cutover_wrapper.py`.
    2. The ownership and freeze tables now list `verify.py` → `verify_cli` and the partial `runner.py` → `task_runner`; the stale "not moved" lines are fixed.
    3. The AH5-12b table is complete: row 2's `exec --ephemeral --json`, row 7's run-directory files, and rows 9–11 for result extraction, the CLI-version and preview path, and provider-error handling. The 3 prompt and context-trust tests are relabelled as Showcase policy.
    4. `test_each_receipt_guard_rejects_with_a_recomputed_receipt_hash`: the forbid-implies-one-invocation and `started_at <= ended_at` guards are now caught. "invocations >= 1" and "status == pass" stay uncaught by design: serialization validation rejects those values before `valid_evidence` sees them, so these are a redundant second layer.
    5. `test_profile_hash_mismatch_refuses_before_any_launch`.
    6. `verify_cli`'s description no longer breaks under `python -OO`.
    7. Counts corrected (`test_verify_cli` 53, `test_task_runner` 20); the consumers matrix shows Showcase on v0.12.0.
    8. A full rehearsal against the current consumer after these fixes; recorded with the release.

## AH5-12b — consumer input and host checks (2026-10-19)

**Showcase's readers of runner output** (Showcase, at `cbbdbca`):
- `orchestrate.py` (`run_started_task`) reads the `provenance.json` path from the task result and uses its
  `base_commit` to prepare the accepted plan.
- `telemetry.reconcile_running` reads and updates `provenance.json` (`status`, `completed_at`, `abandon_reason`;
  `orchestration_id`, `task`).
- `telemetry.summarize` scans `provenance.json` and uses only `provider_metadata.cost_usd`,
  `provider_metadata.usage`, and the top-level `provider`, `status` and `duration_ms`.
- Nothing reads `stdout.log`, `stderr.log`, `exit-code.txt` or `provider-envelope.json` at runtime; they are
  diagnostic. The verification serializer already classifies logs and envelopes as non-authoritative
  (`test_verify.py`).

**Consequences for 12b:**
- **Row 7.** New runs keep writing `provenance.json`, with the same fields, built from the library result. An
  explicit read adapter keeps legacy `provenance.json` readable for `orchestrate`/`telemetry` without rewriting
  historical run directories. Logs and envelopes stay diagnostic-only.
- **Row 8.** `provider_metadata.usage` keeps Showcase's six bounded counters (`input_tokens`, `output_tokens`,
  `total_tokens`, `cached_input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`), mapped from the
  contract's usage units, with unknown kept unknown. `cost_usd` comes only from a provider-reported value (Claude
  `total_cost_usd`), otherwise null, never inferred. Codex `thread_id`/`terminal_event`/`event_count` and Claude
  `session_id`/`duration_ms`/`duration_api_ms`/`num_turns` map from the backend's output where the CLI reports
  them; anything not reported is null and listed.
- **Acceptance tests.** Showcase's `test_telemetry.py`, `test_verify.py`, `test_runner.py` and `test_orchestrate.py`,
  run through the shims, plus a read-adapter binding test in the library-owned `test_cutover_wrapper.py`.

**Row 5 host check on agent-benchmark's only agent host** (this Mac, Darwin 24.6), with the 12b check at `e8b4b9e`:

| CLI | Tool probe | Isolation check |
|---|---|---|
| Claude Code 2.1.289 | exit 0 | Seatbelt blocked the write |
| codex-cli 0.160.0 | exit 0 | `codex sandbox` (read-only) blocked the write |
| OpenCode 1.19.0 | exit 0 | Seatbelt blocked the write |

No CLI that runs on this host today is refused. The check is rerun with the final 12b code before release.

## AH5-12b — differences found while designing Showcase's adapter (reported before implementation)

Row 8 has no change in meaning: Showcase's `provider_metadata` already comes from `agent_harness.telemetry`
(`parse_codex_jsonl`, `parse_claude_envelope`). The adapter applies the same parsers to the backend's `agent-output`,
which gives identical fields. The six points below change meaning and need a decision first.

| # | Today (`runner.py`) | With `AgentCliBackend` | Proposal |
|---|---|---|---|
| D1 | `--model` is optional; without it the CLI uses its own default model. | A request must name a model (the contract's `request.model`). | The runner refuses without `--model`, or Showcase configures a default model per provider in its adapter. Making the model nullable would be a contract change; not proposed. |
| D2 | No time limit (row 6). | A request carries `timeout_seconds`. | A new runner option, `--agent-timeout`, with a Showcase-chosen default, separate from the lease TTL. Showcase's value is needed. |
| D3 | `stderr.log` holds the CLI's stderr. | The backend discards stderr (`stderr=DEVNULL`). | Either the library keeps stderr as a diagnostic artifact (an additive change; benchmark gets one more evidence file), or Showcase drops `stderr.log`, which has no runtime reader. |
| D4 | The runner parses whatever the CLI prints. | Output containing a credential-shaped string is withheld, and the run ends `error`/`PROVIDER_ERROR`. | Accept (fail closed). A run that today would expose a credential in its result is refused. |
| D5 | The run dir can be anywhere. | The evidence root must not be writable by the agent's sandbox (workspace, `/tmp`, `$TMPDIR`), otherwise the request is `rejected`. | Accept. Showcase's run dir (`.agent-runs` in the primary checkout) is outside its linked task worktrees. A run whose worktree *is* the primary checkout would be refused. |
| D6 | Output size is unlimited. | Above `output_limit` (8 MiB) the output is withheld: no result, nothing claimed. | Accept (row 7: no result from incomplete output), or Showcase sets a larger `output_limit`. |

### D1–D6 decisions (master, 2026-10-19); conditions are acceptance criteria

| # | Decision |
|---|---|
| D1 | The model is resolved before launch: `--model` first, then Showcase's versioned per-provider configuration. If both are missing, the run is refused before the attempt starts. No model is chosen by the harness, and the CLI's default model is not relied on. The requested model is recorded, and the actual model separately when the provider reports it. No contract change. |
| D2 | `--agent-timeout`, Showcase default 1800 s, explicitly overridable. It is an operational limit, not an SLO, and not a new library default. The lease is renewed or covers execution plus cancel, drain and writing the result; an inconsistent configuration is refused before launch. |
| D3 | The library keeps stderr as a size-bounded diagnostic artifact, with the same secret check as stdout. Showcase may read `stderr.log` compatibly, but there is no second independent write. Benchmark compatibility is checked. |
| D4 | Suspect output is withheld, with an unambiguous reason code. The suspect content never appears in errors or logs, and the harness never claims a real secret was found. Safe execution metadata is kept. No automatic retry. |
| D5 | The evidence root must be protected from agent writes. Neither the name `.agent-runs` nor a location outside the worktree proves that: the real writable roots, symlinks and sandbox are checked. A primary checkout is refused when protection cannot be assured, with a clear instruction to use an isolated worktree. |
| D6 | The output limit stays at 8 MiB. Exceeding it may withhold the output and block acceptance of the task result, but there is still a terminal contract result. The reason, the limit-exceeded fact, the known process state, cancel/drain and usage are all kept. Truncated output is never parsed as a complete result. |
| Shared rule (D3/D4/D6) | The process outcome, whether artifacts can be published, and whether the task result is accepted are separate facts. A failure to publish output does not prove the agent changed no files. |
| Row 8 | Semantics unchanged: `provider_metadata` already uses the shared parsers. The compatibility check stays; no parser migration. |

**New divergence, raised for confirmation (D4/D6 with the shared rule):**
- Today `_withhold` turns a withheld output into `outcome: error`, `error_code: PROVIDER_ERROR`, even when the CLI
  exited 0. That labels a publication failure as a provider failure. agent-benchmark receives this today.
- The contract's result fields are fixed, so there is no field for a withheld reason.
- Proposal: keep the process's real `outcome`/`exit_code`/`drain`. Publish no candidate and no `agent-output`, and
  add an evidence artifact `withheld.json` with an explicit reason code (`OUTPUT_LIMIT_EXCEEDED` or
  `CREDENTIAL_SHAPED_OUTPUT`), the size and limit, and no content.
- That changes what benchmark sees: a `completed` run without a candidate instead of an `error`. It needs
  benchmark's compatibility check before it lands.
- The alternative keeps today's `error`/`PROVIDER_ERROR` and only adds `withheld.json`. It is compatible, but it
  blurs the process outcome.
