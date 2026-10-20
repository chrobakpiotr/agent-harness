# Changelog

## Unreleased

- New `agent_harness.verification_contract`: the verification-contract validation (AH5-13). The repository root,
  the constitution path and the active-feature layout are explicit; none is built in. Contract authoring stays with
  the consumer.
- The Showcase runner shim's fake CLI finds the interpreter outside `/usr/bin` (Showcase's portability fix).

## 0.14.0 — unreleased (prepared 2026-10-19)

- Showcase's `runner.py` launches its agent through `agent_harness.execution.AgentCliBackend` (AH5-12b). One launch
  path: the runner shim binds Showcase's models, profiles, prompts and provenance; there is no second
  launch/cancel/evidence path. Decisions: `docs/specs/AH5-12/packet.md` (rows 1–11, D1–D6).
- `AgentCliBackend` additions, all additive:
  - `cli_options`: validated launch options (sandbox mode, output schema, strict Codex flags, reasoning, Claude
    tools, denied commands, `deny_read`, max turns and budget, restricted mode, `dontAsk`). None can loosen the
    library minimum.
  - an isolation check before launch: the sandbox the run uses must run a command and block a write into the
    evidence root. The evidence root must be unreachable by agent writes by real paths, symlinks included; a primary
    checkout is refused with the instruction to use a linked worktree. A refusal is `rejected` before anything
    starts or is written.
  - `launch.json` (the argv without the prompt, the requested login mode, `billing_source_confirmed: false`) and
    `agent-stderr` (size-bounded, secret-checked).
  - Claude's file tools are denied the evidence root, beside the sandbox `denyWrite`.
- **Semantic change (agent-benchmark checked it):** withheld output (credential-shaped, or over 8 MiB) keeps the
  process's real `outcome`, `exit_code` and `drain`. Before, it became `error`/`PROVIDER_ERROR`. No candidate and
  no `agent-output` are published, and a `withheld` artifact (`withheld.json`) gives the reason, sizes and limits,
  never the content.
- The Showcase runner shim: `--model` or a configured model (none configured: refused before the run),
  `--agent-timeout` (default 1800 s), `--preflight` (checks without launching), provenance with the process outcome
  and published artifacts, and old run directories read as they are.
- Contract and digests unchanged.

## 0.13.0 — 2026-10-18

- New `agent_harness.verify_cli`: the verification CLI (AH5-11). `main(argv, *, profile_root, store_factory,
  resolve_execution, execute_plan)`: the consumer passes its trusted profile root and its bound store, resolver and
  executor; none is built in. 45 planner, fingerprint and serialization tests that had stayed in Showcase now run
  in the library.
- New `agent_harness.task_runner`: the runner's provider-neutral checks (AH5-12a). These cover packet integrity,
  task-result schema projection and validation, git/path postconditions and the accepted-plan verification hook.
  The tested projection profiles, the canonical schema, the verification CLI, the profile root, the environment
  builder and the binding check are explicit; none is built in.
- Contract and digests unchanged.

## 0.12.0 — 2026-10-18

- New `agent_harness.state_snapshots`: Showcase's write-ahead snapshots of `.agent-state` under `refs/agent-state*`,
  verbatim (AH5-09).
- New `agent_harness.eval_runner`: the harness eval engine (AH5-10). The suites directory and the repository anchor
  are explicit; consumers keep their suites.
- ADR 0001 amendment:
  - one ownership rule: frozen copies from the freeze notice;
  - Showcase's cutover is independent of backend qualification, and every guard stays;
  - a declared supported API: `contract`, `execution`, `qualification` (`qualify`, `probe_digest`,
    `DEFAULT_IMAGE`, `DEFAULT_TARGET`), `qualification.reference.reviewed_reference` and the CLI. A test pins these
    names.
- `docs/migration/plan-remaining-2026-10-18.md`, and current consumer, README and cutover docs.
- Contract and digests unchanged.

## 0.11.0 — 2026-10-18

- New `agent_harness.verification.supervisor`, `.command` and `.executor`: Showcase's verification supervisor,
  command backend and plan executor (AH5-08b).
  - The command backend takes the consumer's `argv_policy`, `blocked_executables` and `sandbox` namespace.
    The default policy (`deny_all`) allows nothing.
  - `run_command` takes `argv_policy`, `store_factory` and `backend_factory`.
  - `execute_plan` takes `lifecycle`, `resolve_execution`, `backend_factory` and `store_factory`. An authority
    run without a lifecycle and a resolver is refused.
- New `agent_harness.verification.retry`: registered retry controls (`RetryControl`, `registry`,
  `retry_policy_facts`).
  - The consumer collects evidence and checks its run directory; the library checks the evidence against the
    registration.
  - `VerificationStore(retry_controls=...)`. With the default empty registry, only the literal no-op control
    proves a critical gate retry-free.
- **Breaking:** Showcase's retry-control registry (the `apps/ecommerce` Gradle gates and their input hashes) is
  no longer in the library store. It had come over unrecorded with the store; Showcase now passes it in.
- Contract and digests unchanged.

## 0.10.0 — 2026-10-18

- New `agent_harness.sandbox`: Showcase's verification sandbox (AH5-08a), covering backend discovery, `doctor()`,
  the isolated environment, the bubblewrap, macOS `sandbox-exec` and Codex plans, adversarial backend
  qualification and the process-group backend. Consumer policy is passed in explicitly:
  `environment_policy(env, sandbox_home)` adds tool variables but cannot replace the isolated base or add
  loader or interpreter hooks; `worktree_wrappers` declares `./name` wrappers; `authority_roots` adds
  control-plane roots relative to the primary checkout. Each is refused when invalid.
- New `agent_harness.verification.workspace`: diagnostic-only full-source workspaces.
- Contract and digests unchanged.

## 0.9.0 — 2026-10-17

- The moved verification modules and `telemetry` are re-synced with Showcase `49ab97f` (SDD-OBS-001): verification
  admission, reservation and consumption records; executable resolution for planned commands; profile
  `toolchain_directories`; provider-metadata filtering in telemetry. Provenance: `docs/migration/provenance.md`.
- New `agent_harness.executables`: `resolve_executable`, `verify_executable_binding`,
  `trusted_executable_directories` and `default_executable_binder` (plain names only; path-like executables are
  refused).
- `execution_plan_record`, `validate_plan_record`, `resolve_execution`, `prepare_task_plan` and `VerificationStore`
  take `executable_binder`. Commands run from the explicit repository, never the process working directory.
- `VerificationStore(issuer_registry_path=…)`: a relative path anchored to the primary checkout; an absolute or
  traversing path is refused (`issuer-registry-path-invalid`). A production store refuses a caller-supplied
  `issuer_registry` (`issuer-registry-override-forbidden`); without a path there is no registry.
- `agent_harness.repository.resolve_primary_worktree`.
- Contract and digests unchanged.

## 0.8.4 — 2026-10-11

- `AgentCliBackend.request_counts[request_id]`: requests the OpenCode relay forwarded to OpenRouter.
- `error_details` `resets_at` is also read from Codex's usage-limit message (`try again at 7:57 PM.`, host local
  time, the next such time); a dated reset stays null.
- Contract and digests unchanged.

## 0.8.3 — 2026-10-11

- `AgentCliBackend("opencode", prompt, api_key_file=…)`: OpenCode on a free OpenRouter model (`vendor/model:free`;
  any other model refused). macOS only: OpenCode runs under a Seatbelt profile whose only network is a 127.0.0.1
  relay that holds the key and forwards only the run's model; no DNS, Keychain, `open URL`, Docker socket or login
  files. The daily free cap gives `error_details` `quota_exhausted` with `resets_at` and stops forwarding. Usage is
  summed from `step_finish` events; unknown cache counts stay null.
- Output scan: existing absolute paths in login files (project paths in `~/.claude.json`) are no longer treated as
  secrets.
- Contract and digests unchanged.

## 0.8.2 — 2026-10-11

- `AgentCliBackend.error_details`: `resets_at` is also read from Claude's real session-limit message (`resets 2:50pm
  (Europe/Warsaw)` → the next such time in that zone), not only from `…|<epoch>`. Verified on the pilot's real quota
  errors. Contract and digests unchanged.

## 0.8.1 — 2026-10-11

- `AgentCliBackend`:
  - a result other than `completed` never carries a candidate (it was sealed before `is_error` became `error`);
  - `error_details[request_id]` classifies provider errors as `quota_exhausted` (with `resets_at` when the CLI
    states it) or `provider_error`.
- Contract and probe digests unchanged; the v0.8.0 reviewed reference stays valid.

## 0.8.0 — 2026-10-11

- `qualification.qualify`:
  - `target_kind` (`docker-desktop`, `github-runner`, `linux-docker`);
  - checks that ended only on a Docker command timeout are retried (`retries`, default 2; probe timeout default
    60 s), with earlier attempts in `evidence/retries.json`;
  - exited probe containers named in the run's own evidence are removed;
  - `grading.probe_grading_layout` qualifies the backend's real grading argv (stdin tar, read-only `/inputs`, working
    directory, exact environment, `/output` cap, timeout) as the tuple fact `grading_layout`.
- The grading backend moved to `agent_harness.grading` (re-exported from `execution`). It is covered by
  `qualification.probe_digest()`, and the backend identifies the live target as the report does.
- The shipped reviewed reference is replaced (job `ref-20261011T082417Z`, grading layout qualified). Sessions on
  v0.7.0 references do not match v0.8.0.

## 0.7.0 — 2026-10-10

- `execution.QualifiedDockerBackend` (AH5-04c-2): grading on the qualified Docker target, contract v2. A fresh container
  per request with exactly the qualified flags; the workspace is streamed into a tmpfs (bounded, `.git`, links and
  hardlinks skipped); `cases.json` is read-only; `answers.json` is read only after the container is confirmed gone.
  `fired: timeout|oom|disk`. Refused before launch on other limits or on drift of the live tuple.
- `agent_harness.qualification` (AH5-04c-1): the Q01–Q16/B1–B10 probes, moved verbatim from Showcase, and
  `qualify(job_id, out, reviewed=…)` with `probe_digest()` and `policy_digest()`. `qualification.reference
  .reviewed_reference()` returns the shipped, independently reviewed Docker Desktop reference.
- Contract: `session_qualification_passes` and `validate_capability_binding(…, reviewed=…)` (ADR 0002 amendment: one
  independent review per tuple and probe code). The backend pins sessions to this library's probe and policy digests.

## 0.6.1 — 2026-10-10

- `AgentCliBackend` (Claude): allow sandboxed Bash outright, so commands that set variables or use heredocs
  (typical test commands) run in `-p` mode, as under Codex `workspace-write`. Smoke test distinguishes a blocked
  login read from a model refusal.

## 0.6.0 — 2026-10-10

- `execution.AgentCliBackend` (AH5-05b): runs `claude -p` / `codex exec` on the CLI's own subscription login, with
  the prompt on stdin and fixed command lines (no caller arguments). API-key and routing variables are removed and
  the login method is pinned. Claude Code's Bash sandbox runs without network, failing closed; Codex runs in
  `--sandbox workspace-write`. A sandbox probe refuses the launch (`CAPABILITY_UNSUPPORTED`, with a reason) instead of
  falling back to another mode. Usage comes from the CLI's own report, and the raw output is a sealed `agent-output`
  artifact. Optional candidate: one workspace file, or `diff_base` (`git diff --binary`, built from a Harness-owned Git
  directory, never the workspace's `.git`). Outputs carrying login credentials are withheld. An evidence root the
  agent's sandbox can write is refused. `ProcessBackend` behaviour unchanged. `scripts/smoke-agent-cli.py` checks
  the real CLIs.

## 0.5.0 — 2026-10-07

- Contract v2 amendment (ADR 0005): candidate targets — an `unqualified` result may carry `target` with
  `qualification_digest: null`, so a target's first qualification can record B10 without its own digest;
  `validate_target(target, qualified=False)`; golden fixture `v2-candidate-target.json`. v0.4 documents stay valid.

## 0.4.0 — 2026-10-07

- Contract v2 (ADR 0005): request `limits`; result `target` (qualified target id, qualification digest, workload
  image digest) and `limits` (applied, fired, output_truncated); `LIMIT_EXCEEDED`. v1 documents unchanged and
  still valid. Offline launch: `ProcessBackend` rejects limits; `ScriptedBackend(fired=…, output_truncated=…)`.
  Golden fixture `v2-limit-exceeded.json`.

## 0.3.0 — 2026-10-06

- Contract v1 qualification report (`validate_qualification_report`, `qualification_passes`,
  `validate_capability_binding`, `verify_qualification_evidence`, `qualification_digest`) with mandatory checks
  Q01–Q16 and B1–B10, and CLI `agent-harness qualification --check` (AH5-04b-r).
- CLI `agent-harness constitution [--digest | --check PATH]`: the shared agent contract ships in the wheel;
  consumers check their verbatim copy for drift (AH5-00b). Constitution 1.0.1 (adds the check command to the
  ownership rule; no rule changes); a version names exactly one text.

## 0.2.0 — 2026-10-06

- **Breaking:** Python >= 3.13 (was >= 3.9). The `v0.1.0` tag keeps its 3.9 floor.
- Public offline launch/cancel API `agent_harness.execution` (ADR 0004): `launch`, `Execution`,
  `ScriptedBackend` (fake), `ProcessBackend` (controlled local process group); contract v1 unchanged.
- Optional extra `grants` (`cryptography==49.0.0`) for Ed25519 human retry grants; the core install keeps no
  third-party dependency.
- Internal (no compatibility promise): verification profile, fingerprint, planner, candidate, store,
  human grants and authority modules moved from Showcase `50c18f9` (AH5-04a-1). Plan acceptance goes through an
  injected lifecycle port; profile root and issuer registry are explicit parameters. Origin admission and
  completion stay blocked.
- Showcase cutover wrapper (`migration/showcase-wrapper/`), rehearsal scripts and operator guide (AH5-06a);
  Showcase itself is unchanged.
- Agent practices (`docs/agentic-sdd/`): `builder`/`evaluator` roles, `diagnosing`, `writing-for-agents`,
  `retro` adapted from mattpocock/skills, handbook, and the `evals/diagnosing` mini eval (AH5-07).

## 0.1.0 — 2026-10-05

- Execution contract v1 (`agent_harness.contract`): `validate_request`, `validate_result`,
  `validate_capability_report`, `validate_repo_context`, `request_digest`, `ContractError`.
- Golden fixtures in the wheel: success, fail, timeout, unknown-terminal, missing-qualification.
- `agent-harness --help/--version` CLI; no other commands.
- Not included: launch/cancel API, execution backends, Showcase runtime modules.
