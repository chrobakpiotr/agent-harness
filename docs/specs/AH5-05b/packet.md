# AH5-05b — Coding-agent CLI backend (subscription login, no spend)

- Status: **implemented**; human smoke test passed 2026-10-10 (both CLIs); independent evaluation pending.
- Source: agent-benchmark AB5-07 (no-spend pilot): a backend that runs a coding-agent CLI on its own login and
  reports the CLI's usage. Extends AH5-05a (ADR 0004); contract v1/v2 unchanged. Independent of Showcase.

## Scope

`agent_harness.execution.AgentCliBackend(provider, prompt, *, candidate=None, diff_base=None, env=None,
grace=2.0, output_limit=8 MiB, sandbox_probe=None)`, a `ProcessBackend` child (own process group, timeout/cancel/drain as AH5-05a):

- Fixed command lines. The request's `provider` must equal the backend's, and its `model` (a contract id) is passed
  through:
  - `claude -p --model <model> --output-format json --permission-mode acceptEdits --settings <sandbox>
    --disallowedTools WebFetch,WebSearch`;
  - `codex exec -m <model> --sandbox workspace-write --skip-git-repo-check --json --ephemeral -`.

  The prompt is always written to stdin, never passed as an argument, so a prompt such as `--bare` cannot become a
  flag.

  No caller arguments, so `--bare`, `bypassPermissions`, `danger-full-access` and `--dangerously-*` cannot appear.
- Login protection: Claude's sandbox denies reads of `~/.claude`, `~/.claude.json`, `~/.codex`, `~/.config/claude`
  (smoke-verified). Codex `workspace-write` can read them, so every candidate and the `agent-output` are scanned for
  the login files' token values and credential shapes; a hit withholds both and reports `error` / `PROVIDER_ERROR`
  (`backend.withheld` says which).
- Claude sandbox settings: `sandbox.enabled`, `failIfUnavailable: true` (no unsandboxed fallback),
  `allowUnsandboxedCommands: false`, `network.allowedDomains: []`, `autoAllowBashIfSandboxed: true`. WebFetch and
  WebSearch run outside the sandbox, so they are disallowed.
- Credentials and routing: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN` and `OPENAI_API_KEY` are
  removed from the child's environment, so the CLI uses its own subscription login.
- Candidate: either `candidate` (one workspace file) or `diff_base` (a full commit id): after `completed` with
  `drain: confirmed`, `git diff --binary <diff_base>` of the whole workspace (untracked included, `.git` never) is
  sealed by digest. Git never reads the workspace's `.git` after the agent ran:
  - before launch, `diff_base` is fetched into a Git directory of Harness's own under the evidence root (refused,
    `LAUNCH_FAILED`, if the evidence root is inside the workspace);
  - the diff runs with `GIT_DIR` = that directory, `GIT_WORK_TREE` = the workspace and a throwaway index;
  - no system or user config, a minimal environment, `core.fsmonitor=false`, `core.hooksPath=/dev/null`,
    `core.attributesFile=/dev/null`, no external diff, `--no-ext-diff --no-textconv`.

  An agent-written `core.fsmonitor`, `filter.<x>.clean` or `.gitattributes` therefore runs nothing on the host
  (regression test from agent-benchmark's PoC; it fails on `0c4a9a0`).
- Refusal before launch (`rejected`, nothing started, nothing written; `backend.rejection_reason` says why):
  - provider mismatch → `CAPABILITY_UNSUPPORTED`;
  - CLI not on `PATH` → `BACKEND_UNAVAILABLE`;
  - sandbox probe fails → `CAPABILITY_UNSUPPORTED`. Codex is probed with `codex sandbox -c sandbox_mode="workspace-write" --
    true`; on Linux, Claude is probed for bubblewrap and socat. There is no fallback mode.
- Output: stdout up to `output_limit` is kept as the named artifact `agent-output` (by digest). Usage becomes one
  `provider` summary:
  - Claude `usage.input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens`, cache semantics
    `separate`;
  - Codex, from the last `turn.completed` (each carries the thread's running total): `input_tokens/output_tokens/cached_input_tokens`, cache semantics
    `included_in_input`; cache writes from `cache_write_input_tokens` when the CLI reports it (codex-cli 0.160),
    else unknown and `partial`.

  `is_error`, a non-`success` subtype, `turn.failed` or unreadable output → `error` / `PROVIDER_ERROR`, keeping any
  usage reported. A single Claude `modelUsage` entry becomes `resolved_model`.
- `isolation_level: controlled`, never qualified; the CLI's sandbox is not a qualified target (04b/ADR 0002).

## Acceptance criteria

- AC1: the exact command lines above, with the prompt on stdin; no billing variable reaches the child; no forbidden
  flag in argv, even when the prompt looks like one.
- AC2: usage from Claude JSON and summed Codex `turn.completed` events; every result validates (v1 and v2).
- AC3: a failed sandbox probe → `rejected` / `CAPABILITY_UNSUPPORTED` with a reason, with the agent never started.
- AC4: provider errors and unreadable output → `error` / `PROVIDER_ERROR`; output over the limit → no usage claimed.
- AC5: cancel stops the CLI's process group (`cancel`, `drain: confirmed`); `ProcessBackend` behaviour unchanged.

Tests: `tests/test_agent_cli.py` (fake `claude`/`codex` on `PATH`; no real provider calls).

## Not verified offline (human smoke test before the pilot)

- That `codex exec … -` reads the prompt from stdin (Claude `-p` does without a prompt argument).
- The `codex sandbox` probe command and whether `workspace-write` starts in the target container.
- That `autoAllowBashIfSandboxed` lets sandboxed Bash (tests) run in `-p` mode without a prompt; the Claude docs do
  not name this key. If it does not, Claude cannot run tests and the two configurations are not equal.
- The Claude JSON usage field names (the docs list only `result`, `session_id`, `total_cost_usd`).
- Smoke test: `PYTHONPATH=src python3 scripts/smoke-agent-cli.py [--provider claude|codex|both]` on the logged-in
  machine. It runs one small task per CLI and checks launch, completion, stdin prompt, sandboxed shell, blocked
  network and usage (exit 0 = all pass).

## Known limits

- `output_limit` is checked after exit; a runaway CLI can still fill the disk (`ponytail` note in code).
- `rejection_reason` is per backend instance; concurrent launches on one instance share it.

## Smoke test result (2026-10-10, macOS, logged-in CLIs, no API key exported)

| CLI | Result |
|---|---|
| `claude` (sonnet → `claude-sonnet-5-5`) | all six checks pass. Sandboxed shell ran without a prompt (`autoAllowBashIfSandboxed` works); `curl` blocked by the sandbox proxy (403). Usage complete: input 4, output 510, cache read 37 328, cache write 20 936. |
| `codex` 0.160 (`gpt-6.1-sol`) | first run rejected: `codex sandbox <os>` is not the 0.160 syntax (`sandbox-exec: execvp() of 'macos' failed`), so the refusal path worked as designed. After fixing the probe to `codex sandbox -c sandbox_mode="workspace-write" -- true`, all six checks pass: stdin prompt, sandboxed shell, network blocked (DNS failure). Usage from `turn.completed`, including `cache_write_input_tokens`. |

Still unverified: the Linux/container sandbox (bubblewrap/Landlock); run the smoke script in the pilot container.
