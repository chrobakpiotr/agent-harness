"""AgentCliBackend (AH5-05b): fixed CLI command lines, no billing credentials, usage from the CLI's own report."""

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from agent_harness import contract, execution

CLAUDE_OK = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": "done",
             "usage": {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 300,
                       "cache_creation_input_tokens": 80}, "modelUsage": {"claude-sonnet-x": {}}}
CODEX_OK = [{"type": "thread.started"}, {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 400, "output_tokens": 70}}]


class AgentCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("workspace", "evidence", "bin"):
            (self.root / name).mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {"PATH": f"{self.root / 'bin'}:/usr/bin:/bin", "HOME": str(self.root),
                    "ANTHROPIC_API_KEY": "sk-ant-secret", "CODEX_API_KEY": "sk-codex-secret",
                    "OPENAI_API_KEY": "sk-openai-secret", "ANTHROPIC_AUTH_TOKEN": "secret-token",
                    "ANTHROPIC_BASE_URL": "https://proxy.invalid", "CLAUDE_CODE_USE_BEDROCK": "1",
                    "CLAUDE_CODE_USE_VERTEX": "1", "CODEX_ACCESS_TOKEN": "codex-token"}

    def fake(self, name, stdout="", *, exit_code=0, probe_exit=0, sleep=0):
        """A fake CLI that logs argv and the billing variables it sees, then prints ``stdout``."""
        script = self.root / "bin" / name
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, sys, time
            argv = sys.argv[1:]
            stdin = "" if argv[:1] in (["sandbox"], ["--version"]) else sys.stdin.read()
            with open({str(self.log)!r}, "a") as log:
                log.write(json.dumps({{"cli": {name!r}, "argv": argv,
                    "stdin": stdin,
                    "billing": sorted(k for k in os.environ if k in {list(execution._BILLING_ENV)!r})}}) + "\\n")
            if argv[:1] in (["sandbox"], ["--version"]):
                sys.exit({probe_exit})
            time.sleep({sleep})
            sys.stdout.write({stdout!r})
            sys.exit({exit_code})
            """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def claude(self, prompt, **kw):
        """Claude backend whose probe is the fake CLI itself (the Linux default checks bubblewrap, tested apart)."""
        return execution.AgentCliBackend("claude", prompt, sandbox_probe=["claude", "--version"], **kw)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def request(self, provider, model, request_id="req-1", version=1):
        doc = {"contract_version": version, "request_id": request_id, "trial_id": None,
               "task_digest": "sha256:" + hashlib.sha256(b"task").hexdigest(),
               "config_digest": "sha256:" + hashlib.sha256(b"config").hexdigest(), "provider": provider,
               "model": model, "settings": {}, "capabilities": ["usage"], "timeout_seconds": 30,
               "max_attempts": 1, "input_bindings": []}
        if version == 2:
            doc["limits"] = None
        return contract.validate_request(doc)

    def run_backend(self, backend, request):
        result = execution.launch(request, backend, workspace=str(self.root / "workspace"),
                                  evidence_root=str(self.root / "evidence")).result(60)
        return contract.validate_result(result, request)

    def test_claude_command_line_usage_and_no_billing_credentials(self):
        self.fake("claude", json.dumps(CLAUDE_OK))
        result = self.run_backend(self.claude("fix the bug", env=self.env),
                                  self.request("claude", "sonnet"))
        self.assertEqual(("completed", 0, "controlled"), (result["outcome"], result["exit_code"],
                                                          result["isolation_level"]))
        run = self.calls()[-1]
        self.assertEqual([], run["billing"])
        argv = run["argv"]
        self.assertEqual(["-p", "--model", "sonnet", "--output-format", "json",
                          "--permission-mode", "acceptEdits"], argv[:7])
        self.assertEqual("fix the bug", run["stdin"])
        settings = json.loads(argv[argv.index("--settings") + 1])["sandbox"]
        self.assertEqual((True, True, False, []), (settings["enabled"], settings["failIfUnavailable"],
                                                   settings["allowUnsandboxedCommands"],
                                                   settings["network"]["allowedDomains"]))
        self.assertEqual("WebFetch,WebSearch", argv[argv.index("--disallowedTools") + 1])
        self.assertIn("~/.codex", settings["filesystem"]["denyRead"])
        for forbidden in ("--bare", "bypassPermissions", "--dangerously-skip-permissions"):
            self.assertNotIn(forbidden, " ".join(argv))
        self.assertEqual({"input_tokens": 120, "output_tokens": 45, "cache_read_tokens": 300,
                          "cache_write_tokens": 80}, result["usage_events"][0]["units"])
        self.assertEqual(("complete", "separate", "provider"),
                         (result["usage_completeness"], result["usage_events"][0]["cache_semantics"],
                          result["usage_events"][0]["source"]))
        self.assertEqual("claude-sonnet-x", result["resolved_model"])
        artifact = result["artifacts"][0]
        data = (self.root / "evidence" / artifact["path"]).read_bytes()
        self.assertEqual(("agent-output", "sha256:" + hashlib.sha256(data).hexdigest()),
                         (artifact["name"], artifact["sha256"]))

    def test_codex_command_line_and_usage_from_the_last_cumulative_turn(self):
        two_turns = CODEX_OK + [{"type": "turn.completed",  # Codex reports the thread total in every turn
                                 "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 75}}]
        self.fake("codex", "\n".join(json.dumps(e) for e in two_turns) + "\n")
        result = self.run_backend(execution.AgentCliBackend("codex", "fix the bug", env=self.env),
                                  self.request("codex", "gpt-6.1-sol", version=2))
        probe, run = self.calls()
        self.assertEqual("sandbox", probe["argv"][0])
        self.assertEqual(["exec", "-m", "gpt-6.1-sol", "--sandbox", "workspace-write", "--skip-git-repo-check",
                          "--json", "--ephemeral", "-"], run["argv"])
        self.assertEqual("fix the bug", run["stdin"])
        self.assertEqual([], run["billing"])
        self.assertEqual({"input_tokens": 1000, "output_tokens": 75, "cache_read_tokens": 400,
                          "cache_write_tokens": None}, result["usage_events"][0]["units"])  # older CLI: no writes
        self.assertEqual(("partial", "included_in_input"),
                         (result["usage_completeness"], result["usage_events"][0]["cache_semantics"]))

    def test_codex_cache_writes_make_usage_complete(self):
        turn = {"type": "turn.completed", "usage": {"input_tokens": 34144, "cached_input_tokens": 29056,
                                                     "cache_write_input_tokens": 0, "output_tokens": 142}}
        self.fake("codex", json.dumps(turn) + "\n")
        result = self.run_backend(execution.AgentCliBackend("codex", "x", env=self.env), self.request("codex", "m"))
        self.assertEqual(({"input_tokens": 34144, "output_tokens": 142, "cache_read_tokens": 29056,
                           "cache_write_tokens": 0}, "complete"),
                         (result["usage_events"][0]["units"], result["usage_completeness"]))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root / "workspace"), *args], check=True,
                              capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}).stdout

    def test_diff_candidate_seals_the_whole_workspace_change_after_drain(self):
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("old\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        script = self.root / "bin" / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, pathlib
            if sys.argv[1:2] == ["--version"]: sys.exit(0)
            sys.stdin.read()
            pathlib.Path("a.txt").write_text("new\\n"); pathlib.Path("b.bin").write_bytes(bytes(range(256)))
            print({json.dumps(CLAUDE_OK)!r})
            """))
        script.chmod(0o755)
        result = self.run_backend(self.claude("x", env=self.env, diff_base=base), self.request("claude", "sonnet"))
        sealed = (self.root / "evidence" / result["candidate"]["path"]).read_bytes()
        self.assertIn(b"-old\n+new", sealed)
        self.assertIn(b"GIT binary patch", sealed)  # untracked binary file included
        self.assertEqual(b"", self.git("diff", "--cached"))  # the workspace index is untouched
        with self.assertRaises(ValueError):
            self.claude("x", candidate="a.txt", diff_base=base)
        with self.assertRaises(ValueError):
            self.claude("x", diff_base="HEAD")

    def test_login_token_in_candidate_or_output_is_withheld(self):
        token = "tok-" + "x" * 40
        (self.root / ".codex").mkdir()
        (self.root / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"refresh": token}}))
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("old\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        script = self.root / "bin" / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, pathlib
            if sys.argv[1:2] == ["--version"]: sys.exit(0)
            sys.stdin.read()
            pathlib.Path("stolen.txt").write_text({token!r})
            print({json.dumps(CLAUDE_OK)!r})
            """))
        script.chmod(0o755)
        backend = self.claude("x", env=self.env, diff_base=base)
        result = self.run_backend(backend, self.request("claude", "sonnet"))
        self.assertEqual(("error", "PROVIDER_ERROR", None, [], "candidate"),
                         (result["outcome"], result["error_code"], result["candidate"], result["artifacts"],
                          backend.withheld))
        self.assertEqual([], list((self.root / "evidence").rglob("candidate")))
        self.fake("claude", json.dumps({**CLAUDE_OK, "result": "key sk-ant-" + "a" * 30}))
        backend = self.claude("x", env=self.env)
        result = self.run_backend(backend, self.request("claude", "sonnet", request_id="req-2"))
        self.assertEqual(("error", [], "agent-output"), (result["outcome"], result["artifacts"], backend.withheld))

    def test_codex_sandbox_unavailable_is_rejected_without_running_the_agent(self):
        self.fake("codex", json.dumps(CODEX_OK[-1]), probe_exit=1)
        backend = execution.AgentCliBackend("codex", "fix", env=self.env)
        result = self.run_backend(backend, self.request("codex", "gpt-6.1-sol"))
        self.assertEqual(("rejected", "CAPABILITY_UNSUPPORTED"), (result["outcome"], result["error_code"]))
        self.assertIn("sandbox unavailable", backend.rejection_reason)
        self.assertEqual(["sandbox"], [c["argv"][0] for c in self.calls()])  # probe only, no exec
        self.assertFalse((self.root / "evidence" / "executions").exists())

    def test_provider_errors_become_error_results(self):
        self.fake("claude", json.dumps({**CLAUDE_OK, "is_error": True, "subtype": "error_max_turns"}))
        result = self.run_backend(self.claude("x", env=self.env), self.request("claude", "sonnet"))
        self.assertEqual(("error", "PROVIDER_ERROR"), (result["outcome"], result["error_code"]))
        self.assertEqual(120, result["usage_events"][0]["units"]["input_tokens"])  # spent tokens are still reported
        self.fake("codex", json.dumps({"type": "turn.failed", "error": {"message": "boom"}}) + "\n")
        result = self.run_backend(execution.AgentCliBackend("codex", "x", env=self.env),
                                  self.request("codex", "m", request_id="req-2"))
        self.assertEqual(("error", "PROVIDER_ERROR", "unknown"),
                         (result["outcome"], result["error_code"], result["usage_completeness"]))

    def test_unreadable_or_oversized_output_reports_no_usage(self):
        self.fake("claude", "not json")
        result = self.run_backend(self.claude("x", env=self.env), self.request("claude", "sonnet"))
        self.assertEqual(("error", "unknown", []), (result["outcome"], result["usage_completeness"],
                                                    result["usage_events"]))
        self.fake("claude", json.dumps(CLAUDE_OK))
        result = self.run_backend(self.claude("x", env=self.env, output_limit=10),
                                  self.request("claude", "sonnet", request_id="req-2"))
        self.assertEqual(("unknown", []), (result["usage_completeness"], result["artifacts"]))

    def test_mismatched_request_or_missing_cli_is_refused_before_launch(self):
        self.fake("claude", json.dumps(CLAUDE_OK))
        backend = self.claude("x", env=self.env)
        self.assertEqual("CAPABILITY_UNSUPPORTED", self.run_backend(backend, self.request("codex", "sonnet"))["error_code"])
        backend = execution.AgentCliBackend("codex", "x", env=self.env)  # no fake codex on PATH
        result = self.run_backend(backend, self.request("codex", "m", request_id="r3"))
        self.assertEqual(("rejected", "BACKEND_UNAVAILABLE"), (result["outcome"], result["error_code"]))
        self.assertEqual([], [c for c in self.calls() if c["argv"][:1] not in (["--version"],)])

    def test_cancel_stops_the_cli(self):
        self.fake("claude", json.dumps(CLAUDE_OK), sleep=30)
        launched = execution.launch(self.request("claude", "sonnet"),
                                    self.claude("x", env=self.env, grace=0.5),
                                    workspace=str(self.root / "workspace"), evidence_root=str(self.root / "evidence"))
        for _ in range(100):
            if any(c["argv"][:1] == ["-p"] for c in self.calls()):
                break
            execution.time.sleep(0.05)
        launched.cancel()
        result = launched.result(30)
        self.assertEqual(("cancel", "confirmed"), (result["outcome"], result["drain"]))

    def test_prompt_that_looks_like_a_flag_stays_on_stdin(self):
        self.fake("claude", json.dumps(CLAUDE_OK))
        self.run_backend(self.claude("--dangerously-skip-permissions", env=self.env), self.request("claude", "sonnet"))
        run = self.calls()[-1]
        self.assertNotIn("--dangerously-skip-permissions", run["argv"])
        self.assertEqual("--dangerously-skip-permissions", run["stdin"])

    def test_default_probes_use_the_run_sandbox(self):
        self.assertEqual(["codex", "sandbox", "-c", 'sandbox_mode="workspace-write"', "--", "true"],
                         execution._default_probe("codex"))
        with unittest.mock.patch.object(execution.sys, "platform", "linux"):
            self.assertIn("bwrap", execution._default_probe("claude")[-1])
        with unittest.mock.patch.object(execution.sys, "platform", "darwin"):
            self.assertEqual(["claude", "--version"], execution._default_probe("claude"))

    def test_constructor_rejects_unknown_provider_and_empty_prompt(self):
        with self.assertRaises(ValueError):
            execution.AgentCliBackend("gemini", "x")
        with self.assertRaises(ValueError):
            self.claude("")
        self.assertNotIn("ANTHROPIC_API_KEY", self.claude("x", env=self.env).env)


if __name__ == "__main__":
    unittest.main()
