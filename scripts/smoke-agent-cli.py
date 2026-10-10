#!/usr/bin/env python3
"""Human smoke test for AgentCliBackend (AH5-05b) against the real, logged-in `claude` and `codex` CLIs.

Run on the machine where the CLIs are logged in, with no API key exported:

    PYTHONPATH=src python3 scripts/smoke-agent-cli.py [--provider claude|codex|both]

For each provider it launches one small task in a fresh temporary workspace and reports what the offline tests
cannot prove:
  - the CLI runs on its login (no API key in the environment) and the sandbox probe passes, or the refusal reason;
  - the prompt arrives on stdin (the agent creates hello.txt);
  - shell commands run in the sandbox (ran.txt);
  - network is blocked for shell commands (net.txt must stay empty);
  - Claude's sandbox cannot read the CLI login files (leak.txt must stay empty);
  - usage is read from the CLI's report.
Each run spends one small task of subscription quota, no money.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from agent_harness import contract, execution

PROMPT = """Do exactly these three steps in the current directory, then stop:
1. Create a file hello.txt containing the single word: hi
2. Run this shell command: echo ok > ran.txt
3. Run this shell command and ignore any error: curl -sS --max-time 10 https://example.com -o net.txt
4. Run this shell command and ignore any error: (head -c1 ~/.claude.json || head -c1 ~/.codex/auth.json) >/dev/null 2>&1 && echo readable > leak.txt
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
    backend = execution.AgentCliBackend(provider, PROMPT, candidate="hello.txt")
    result = execution.launch(request(provider, model), backend, workspace=str(workspace),
                              evidence_root=str(evidence)).result(900)

    def text(name):
        path = workspace / name
        return path.read_text(errors="replace").strip() if path.is_file() else None

    net = text("net.txt")
    checks = {
        "launched (not rejected)": result["outcome"] != "rejected",
        "completed": result["outcome"] == "completed",
        "prompt via stdin (hello.txt == hi)": text("hello.txt") == "hi",
        "shell ran in sandbox (ran.txt == ok)": text("ran.txt") == "ok",
        "network blocked (net.txt empty or absent)": not net,
        "usage reported": bool(result["usage_events"]),
    }
    leak = text("leak.txt")
    if provider == "claude":  # Codex's workspace-write sandbox can read the login; the output scan covers that
        checks["login files unreadable in the sandbox (leak.txt empty)"] = not leak
    else:
        print(f"info: login files readable by sandboxed commands: {'yes' if leak else 'no'}")
    print(f"\n=== {provider} ({model}) — workspace {workspace}")
    print(f"outcome={result['outcome']} error_code={result['error_code']} exit_code={result['exit_code']} "
          f"drain={result['drain']} resolved_model={result['resolved_model']}")
    if backend.rejection_reason:
        print(f"rejection_reason: {backend.rejection_reason}")
    for event in result["usage_events"]:
        print(f"usage ({result['usage_completeness']}, {event['cache_semantics']}): {event['units']}")
    for artifact in result["artifacts"]:
        output = (evidence / artifact["path"]).read_bytes()
        print(f"agent-output: {artifact['size']} bytes; last 600 bytes:\n{output[-600:].decode(errors='replace')}")
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
