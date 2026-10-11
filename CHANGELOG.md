# Changelog

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
