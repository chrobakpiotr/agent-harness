"""AH5-12b: consumer launch options, the launch record and the sandbox isolation check of AgentCliBackend.

Without options a run is exactly what it was (agent-benchmark's runs); the options only ever add restrictions.
"""
import json
import unittest
import unittest.mock
from pathlib import Path

from test_agent_cli import CLAUDE_OK, CliCase

from agent_harness import execution

CODEX_STREAM = "\n".join(json.dumps(e) for e in (
    {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},))


class LaunchOptionsTest(CliCase):
    def last_argv(self, cli):
        return [c["argv"] for c in self.calls() if c["cli"] == cli and c["argv"][:1] not in (["sandbox"], ["--version"])][-1]

    def launch_record(self, request_id="req-1"):
        return json.loads((self.root / "evidence" / f"executions/{execution._slot(request_id)}/launch.json").read_text())

    def test_without_options_the_command_lines_are_unchanged_and_keys_never_reach_the_cli(self):
        self.fake("codex", CODEX_STREAM)
        self.run_backend(execution.AgentCliBackend("codex", "x", env=self.env), self.request("codex", "m"))
        self.assertEqual(["exec", "-m", "m", "--sandbox", "workspace-write", "-c", 'forced_login_method="chatgpt"',
                          "--skip-git-repo-check", "--json", "--ephemeral", "-"], self.last_argv("codex"))
        self.fake("claude", json.dumps(CLAUDE_OK))
        self.run_backend(self.claude("x", env=self.env), self.request("claude", "sonnet", request_id="req-2"))
        argv = self.last_argv("claude")
        self.assertEqual(["-p", "--model", "sonnet", "--output-format", "json", "--permission-mode", "acceptEdits"],
                         argv[:7])
        self.assertEqual(["--disallowedTools", "WebFetch,WebSearch"], argv[-2:])
        # The environment held API keys and tokens beside the CLI's login: none reached either CLI.
        self.assertEqual({()}, {tuple(c["billing"]) for c in self.calls()})

    def test_codex_options_give_the_reviewer_read_only_and_the_strict_structured_launch(self):
        self.fake("codex", CODEX_STREAM)
        options = {"sandbox_mode": "read-only", "strict": True, "reasoning": "medium", "login_method": "chatgpt",
                   "output_schema": {"type": "object", "properties": {}}}
        self.run_backend(execution.AgentCliBackend("codex", "x", env=self.env, cli_options=options),
                         self.request("codex", "m"))
        argv = self.last_argv("codex")
        self.assertEqual("read-only", argv[argv.index("--sandbox") + 1])
        for flag in ('approval_policy="never"', 'web_search="disabled"', "sandbox_workspace_write.network_access=false",
                     'model_reasoning_effort="medium"', 'forced_login_method="chatgpt"'):
            self.assertIn(flag, argv)
        schema = Path(argv[argv.index("--output-schema") + 1])
        self.assertEqual(options["output_schema"], json.loads(schema.read_text()))
        self.assertTrue(argv[argv.index("--output-last-message") + 1].endswith("/last-message"))
        self.assertEqual("-", argv[-1])  # the prompt still goes on stdin

    def test_claude_reviewer_cannot_write_through_tools_or_sandboxed_bash(self):
        self.fake("claude", json.dumps(CLAUDE_OK))
        options = {"sandbox_mode": "read-only", "tools": ["Read", "Glob", "Grep", "Bash"], "restricted": True,
                   "permission_mode": "dontAsk", "denied_commands": ["Bash(git push *)"],
                   "deny_read": ["~/.ssh"], "max_turns": 7, "max_budget_usd": 1.5,
                   "output_schema": {"type": "object", "properties": {}}}
        self.run_backend(self.claude("review", env=self.env, cli_options=options), self.request("claude", "sonnet"))
        argv = self.last_argv("claude")
        settings = json.loads(argv[argv.index("--settings") + 1])
        workspace = str((self.root / "workspace").resolve())
        self.assertIn(workspace, settings["sandbox"]["filesystem"]["denyWrite"])  # sandboxed Bash cannot write
        self.assertTrue({"Edit", "Write", "NotebookEdit"} <= set(settings["permissions"]["deny"]))
        self.assertIn("~/.ssh", settings["sandbox"]["filesystem"]["denyRead"])
        self.assertTrue(set(execution._LOGIN_PATHS) <= set(settings["sandbox"]["filesystem"]["denyRead"]))
        self.assertEqual([], settings["sandbox"]["network"]["allowedDomains"])
        self.assertFalse(settings["sandbox"]["allowUnsandboxedCommands"])
        self.assertEqual("WebFetch,WebSearch,Bash(git push *)", argv[argv.index("--disallowedTools") + 1])
        self.assertEqual("dontAsk", argv[argv.index("--permission-mode") + 1])
        self.assertTrue({"--restricted", "--strict-mcp-config", "--no-session-persistence"} <= set(argv))
        effective = self.launch_record()["effective_permissions"]
        self.assertEqual([], effective["write_tools"])
        self.assertIn(workspace, effective["sandbox_deny_write"])
        self.assertEqual("strict", effective["mcp"])

    def test_options_that_would_loosen_the_minimum_are_refused(self):
        for provider, options in (("claude", {"tools": ["Read", "WebFetch"]}),
                                  ("claude", {"permission_mode": "acceptEdits"}),
                                  ("claude", {"permission_mode": "bypassPermissions"}),
                                  ("claude", {"sandbox_mode": "read-only", "tools": ["Read", "Write"]}),
                                  ("claude", {"denied_commands": ["git push"]}),
                                  ("codex", {"login_method": "api-key"}),
                                  ("codex", {"sandbox_mode": "danger-full-access"}),
                                  ("codex", {"tools": ["Read"]}),
                                  ("opencode", {"sandbox_mode": "read-only"})):
            with self.subTest(provider=provider, options=options), self.assertRaises(ValueError):
                execution.AgentCliBackend(provider, "x", env=self.env, cli_options=options)

    def test_launch_record_has_no_prompt_and_never_claims_a_billing_source(self):
        self.fake("claude", json.dumps(CLAUDE_OK))
        prompt = "fix the bug — naïve ✓"
        self.run_backend(self.claude(prompt, env=self.env), self.request("claude", "sonnet"))
        raw = (self.root / "evidence" / f"executions/{execution._slot('req-1')}/launch.json").read_text()
        record = json.loads(raw)
        self.assertNotIn("fix the bug", raw)
        self.assertEqual("stdin", record["prompt"]["channel"])
        self.assertEqual(len(prompt.encode("utf-8")), record["prompt"]["bytes"])
        self.assertFalse(record["billing_source_confirmed"])
        self.assertEqual("forceLoginMethod=claudeai", record["requested_login"])
        self.assertEqual("removed", record["credential_environment"])
        stdin = [c["stdin"] for c in self.calls() if c["argv"][:1] == ["-p"]][-1]
        self.assertEqual(prompt, stdin)  # full prompt, encoding intact

    def test_isolation_check_refuses_before_any_launch(self):
        self.fake("codex", CODEX_STREAM)
        cases = {"a write was not blocked": ["sh", "-c", f"echo {execution._ISOLATION_MARKER}; echo x > {{target}}"],
                 "the sandbox never ran": ["false"]}
        for name, probe in cases.items():
            with self.subTest(name):
                backend = execution.AgentCliBackend("codex", "x", env=self.env, isolation_probe=probe)
                result = self.run_backend(backend, self.request("codex", "m", request_id=f"req-{len(name)}"))
                self.assertEqual(("rejected", "CAPABILITY_UNSUPPORTED"), (result["outcome"], result["error_code"]))
                self.assertIn("isolation", backend.rejection_reason)
        self.assertFalse([c for c in self.calls() if c["argv"][:1] == ["exec"]])  # no agent process started


if __name__ == "__main__":
    unittest.main()
