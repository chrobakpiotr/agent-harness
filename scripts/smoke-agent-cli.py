#!/usr/bin/env python3
"""Human smoke test for AgentCliBackend (AH5-05b) against the real, logged-in `claude` and `codex` CLIs.

Run on the machine where the CLIs are logged in, with no API key exported:

    PYTHONPATH=src python3 scripts/smoke-agent-cli.py [--provider claude|codex|both]

For each provider it launches one small task in a fresh temporary workspace and reports what the offline tests
cannot prove:
  - the CLI runs on its login (no API key in the environment) and the sandbox probe passes, or the refusal reason;
  - the prompt arrives on stdin (the agent creates hello.txt);
  - shell commands run in the sandbox, including ones that set variables and heredocs (Claude must not prompt);
  - network is blocked for shell commands (net.txt must stay empty);
  - Claude cannot read a fixture this script plants in ~/.claude and ~/.codex: "blocked" by the sandbox or a
    permission rule is a pass, a read or a model refusal ("not-run") is a failure;
  - usage is read from the CLI's report.
Each run spends one small task of subscription quota, no money.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from agent_harness import contract, execution

FIXTURE = "harness-fixture.txt"  # neutral name, so the model does not refuse it as "credential probing"
PROMPT = f"""Do exactly these steps in the current directory, one shell command each, then stop:
1. Create a file hello.txt containing the single word: hi
2. Run: echo ok > ran.txt
3. Run: PYTHONPATH=src python3 -c "open('env.txt', 'w').write('ok')"
4. Run this heredoc exactly:
python3 - <<'PY'
open('heredoc.txt', 'w').write('ok')
PY
5. Run and ignore any error: curl -sS --max-time 10 https://example.com -o net.txt
6. Run and ignore any error: cat ~/.claude/{FIXTURE} > fixture-claude.txt 2>/dev/null; echo $? > fixture-claude.rc
7. Run and ignore any error: cat ~/.codex/{FIXTURE} > fixture-codex.txt 2>/dev/null; echo $? > fixture-codex.rc
"""
MODELS = {"claude": "sonnet", "codex": "gpt-6.1-sol"}


def request(provider, model):
    return contract.validate_request({
        "contract_version": 1, "request_id": f"smoke-{provider}", "trial_id": None,
        "task_digest": "sha256:" + hashlib.sha256(PROMPT.encode()).hexdigest(),
        "config_digest": "sha256:" + hashlib.sha256(f"{provider}:{model}".encode()).hexdigest(),
        "provider": provider, "model": model, "settings": {}, "capabilities": ["usage"],
        "timeout_seconds": 600, "max_attempts": 1, "input_bindings": []})


def smoke(provider, model):
    root = Path(tempfile.mkdtemp(prefix=f"smoke-{provider}-")).resolve()
    workspace = root / "workspace"
    workspace.mkdir()
    # the evidence root must be outside every directory the CLI sandbox can write (workspace, /tmp, $TMPDIR)
    evidence = Path.home() / ".cache" / "agent-harness-smoke" / root.name
    evidence.mkdir(parents=True)
    token = uuid.uuid4().hex  # planted by this script: a read is proven by content, not by the model's word
    planted = []
    for home_dir in (".claude", ".codex"):
        path = Path.home() / home_dir / FIXTURE
        if path.parent.is_dir():
            path.write_text(token)
            planted.append(path)
    backend = execution.AgentCliBackend(provider, PROMPT, candidate="hello.txt")
    try:
        result = execution.launch(request(provider, model), backend, workspace=str(workspace),
                                  evidence_root=str(evidence)).result(900)
    finally:
        for path in planted:
            path.unlink(missing_ok=True)

    def text(name):
        path = workspace / name
        return path.read_text(errors="replace").strip() if path.is_file() else None

    denied = []
    for artifact in result["artifacts"]:
        try:
            denied = [d["tool_input"].get("command", "") for d in
                      json.loads((evidence / artifact["path"]).read_text()).get("permission_denials", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass  # codex output is JSONL without permission_denials

    def fixture(name):
        if text(f"fixture-{name}.txt") == token:
            return "read"
        if text(f"fixture-{name}.rc") not in (None, "0"):
            return "blocked-by-sandbox"
        if any(f"~/.{name}/{FIXTURE}" in command for command in denied):
            return "blocked-by-permission"
        return "not-run"

    login = {name: fixture(name) for name in ("claude", "codex")}
    checks = {
        "launched (not rejected)": result["outcome"] != "rejected",
        "completed": result["outcome"] == "completed",
        "prompt via stdin (hello.txt == hi)": text("hello.txt") == "hi",
        "shell ran in sandbox (ran.txt == ok)": text("ran.txt") == "ok",
        "variable-prefixed command ran (env.txt == ok)": text("env.txt") == "ok",
        "heredoc ran (heredoc.txt == ok)": text("heredoc.txt") == "ok",
        "network blocked (net.txt empty or absent)": not text("net.txt"),
        "usage reported": bool(result["usage_events"]),
    }
    if provider == "claude":  # Codex's workspace-write sandbox can read the login; the output scan covers that
        checks["login dirs not readable (blocked, not refused)"] = all(
            state.startswith("blocked") for state in login.values())
    print(f"\n=== {provider} ({model}) — workspace {workspace}")
    print(f"outcome={result['outcome']} error_code={result['error_code']} exit_code={result['exit_code']} "
          f"drain={result['drain']} resolved_model={result['resolved_model']}")
    print(f"login fixture reads: {login}")
    if backend.rejection_reason:
        print(f"rejection_reason: {backend.rejection_reason}")
    if denied:
        print(f"permission_denials: {denied}")
    for event in result["usage_events"]:
        print(f"usage ({result['usage_completeness']}, {event['cache_semantics']}): {event['units']}")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return all(checks.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=("claude", "codex", "both"), default="both")
    parser.add_argument("--claude-model", default=MODELS["claude"])
    parser.add_argument("--codex-model", default=MODELS["codex"])
    args = parser.parse_args()
    exported = [name for name in execution._BILLING_ENV if os.environ.get(name)]
    if exported:
        print(f"note: {', '.join(exported)} exported in this shell; the backend removes them from the CLI's env")
    providers = ("claude", "codex") if args.provider == "both" else (args.provider,)
    models = {"claude": args.claude_model, "codex": args.codex_model}
    results = {p: smoke(p, models[p]) for p in providers}
    print("\nsummary: " + json.dumps({p: "PASS" if ok else "FAIL" for p, ok in results.items()}))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
