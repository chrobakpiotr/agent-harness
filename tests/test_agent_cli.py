"""AgentCliBackend (AH5-05b): fixed CLI command lines, no billing credentials, usage from the CLI's own report."""

import datetime
import hashlib
import http.server
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import unittest.mock
from pathlib import Path

from agent_harness import contract, execution

# Written out literally: the test must not derive what to check from the code under test.
BILLING_NAMES = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY"}
CLAUDE_OK = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": "done",
             "usage": {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 300,
                       "cache_creation_input_tokens": 80}, "modelUsage": {"claude-sonnet-x": {}}}
CODEX_OK = [{"type": "thread.started"}, {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 400, "output_tokens": 70}}]


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("workspace", "evidence", "bin"):
            (self.root / name).mkdir()
        self.log = self.root / "calls.jsonl"
        self.real_writable_roots = execution._agent_writable_roots
        # The test roots live under $TMPDIR; treat only the workspace as agent-writable here (tested apart below).
        patcher = unittest.mock.patch.object(execution, "_agent_writable_roots",
                                             lambda ws: [Path(ws).resolve()])
        patcher.start()
        self.addCleanup(patcher.stop)
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
                    "billing": sorted(k for k in os.environ if k in {sorted(BILLING_NAMES)!r})}}) + "\\n")
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


class AgentCliTest(CliCase):
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
        claude_settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(["Bash"], claude_settings["permissions"]["allow"])  # sandboxed; `VAR=x cmd` must not prompt
        self.assertIn("Read(~/.codex/**)", claude_settings["permissions"]["deny"])
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
        self.assertEqual(["exec", "-m", "gpt-6.1-sol", "--sandbox", "workspace-write",
                          "-c", 'forced_login_method="chatgpt"', "--skip-git-repo-check",
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

    def test_diff_never_runs_git_config_the_agent_wrote(self):
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("old\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        marker_a, marker_b = self.root / "fsmonitor-ran", self.root / "filter-ran"
        script = self.root / "bin" / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, pathlib
            if sys.argv[1:2] == ["--version"]: sys.exit(0)
            sys.stdin.read()
            with open(".git/config", "a") as config:
                config.write("[core]\\n\\tfsmonitor = touch {marker_a}\\n"
                             "[filter \\"x\\"]\\n\\tclean = touch {marker_b}; cat\\n")
            pathlib.Path(".gitattributes").write_text("* filter=x\\n")
            pathlib.Path("a.txt").write_text("new\\n")
            print({json.dumps(CLAUDE_OK)!r})
            """))
        script.chmod(0o755)
        result = self.run_backend(self.claude("x", env=self.env, diff_base=base), self.request("claude", "sonnet"))
        sealed = (self.root / "evidence" / result["candidate"]["path"]).read_bytes()
        self.assertFalse(marker_a.exists() or marker_b.exists(), "agent-written git config ran on the host")
        self.assertIn(b"+new", sealed)
        self.assertNotIn(b" .git/", sealed.replace(b"a/.gitattributes", b"").replace(b"b/.gitattributes", b""))
        self.assertNotIn(b"diff --git a/.git/", sealed)

    def test_hardlink_in_the_workspace_withholds_the_diff_candidate(self):
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("x\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        secret = self.root / "host-file"
        secret.write_text("host content\n")
        script = self.root / "bin" / "claude"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os, sys
            if sys.argv[1:2] == ["--version"]: sys.exit(0)
            sys.stdin.read()
            os.link({str(secret)!r}, "linked.txt")
            print({json.dumps(CLAUDE_OK)!r})
            """))
        script.chmod(0o755)
        backend = self.claude("x", env=self.env, diff_base=base)
        result = self.run_backend(backend, self.request("claude", "sonnet"))
        self.assertEqual(("error", None, "candidate"), (result["outcome"], result["candidate"], backend.withheld))

    def test_provider_error_has_no_candidate_and_quota_exhaustion_is_classified(self):
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("x\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        quota = {**CLAUDE_OK, "is_error": True, "subtype": "success", "result": "Claude AI usage limit reached|1760140800"}
        self.fake("claude", json.dumps(quota))
        backend = self.claude("x", env=self.env, diff_base=base)
        result = self.run_backend(backend, self.request("claude", "sonnet"))
        self.assertEqual(("error", None), (result["outcome"], result["candidate"]))
        self.assertEqual([], list((self.root / "evidence").rglob("candidate")))
        self.assertEqual(("quota_exhausted", 1760140800), (backend.error_details["req-1"]["kind"],
                                                           backend.error_details["req-1"]["resets_at"]))
        self.fake("codex", json.dumps({"type": "error", "message": "You've hit your usage limit."}) + "\n"
                  + json.dumps(CODEX_OK[-1]) + "\n")
        codex = execution.AgentCliBackend("codex", "x", env=self.env)
        self.run_backend(codex, self.request("codex", "m", request_id="req-2"))
        self.assertEqual(("quota_exhausted", None), (codex.error_details["req-2"]["kind"],
                                                     codex.error_details["req-2"]["resets_at"]))
        self.fake("claude", json.dumps({**CLAUDE_OK, "is_error": True, "result": "tool crashed"}))
        other = self.claude("x", env=self.env)
        self.run_backend(other, self.request("claude", "sonnet", request_id="req-3"))
        self.assertEqual("provider_error", other.error_details["req-3"]["kind"])

    def test_reset_time_from_the_real_claude_session_limit_message(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        message = "You've hit your session limit · resets 2:50pm (Europe/Warsaw)"
        warsaw = ZoneInfo("Europe/Warsaw")
        before = datetime(2026, 10, 11, 12, 0, tzinfo=warsaw)
        self.assertEqual(int(datetime(2026, 10, 11, 14, 50, tzinfo=warsaw).timestamp()),
                         execution._reset_time(message, now=before))
        after = datetime(2026, 10, 11, 15, 0, tzinfo=warsaw)  # already past: the next day
        self.assertEqual(int(datetime(2026, 10, 12, 14, 50, tzinfo=warsaw).timestamp()),
                         execution._reset_time(message, now=after))
        self.assertIsNone(execution._reset_time("resets soon (Mars/Base)"))
        self.assertEqual("quota_exhausted", execution._provider_error(json.dumps(
            {"is_error": True, "result": message}).encode())["kind"])

    def test_encoded_login_token_is_caught_in_any_alignment(self):
        import base64
        token = b"tok-" + b"q" * 40
        for prefix in (b"", b"a", b"ab", b"abc"):
            for encode in (base64.b64encode, base64.urlsafe_b64encode):
                blob = b"answer: " + encode(prefix + token + b"tail") + b"\n"
                self.assertTrue(execution._leaks(blob, {token}), (prefix, encode.__name__))
            self.assertTrue(execution._leaks(b"x" + (prefix + token).hex().encode(), {token}))
        self.assertFalse(execution._leaks(b"nothing secret here", {token}))

    def test_paths_in_login_files_are_not_secrets(self):
        (self.root / ".claude.json").write_text(json.dumps({"projectPath": str(self.root / "workspace"),
                                                            "token": "t" * 30}))
        self.assertEqual({b"t" * 30}, execution._login_secrets(str(self.root)))

    def test_evidence_root_writable_by_the_agent_is_refused(self):
        ws = self.root / "workspace"
        self.git("init", "-q")
        (ws / "a.txt").write_text("x\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.fake("claude", json.dumps(CLAUDE_OK))
        (ws / "evidence").mkdir()
        request = self.request("claude", "sonnet")
        result = execution.launch(request, self.claude("x", env=self.env, diff_base=base), workspace=str(ws),
                                  evidence_root=str(ws / "evidence")).result(60)
        self.assertEqual(("rejected", "CAPABILITY_UNSUPPORTED"), (result["outcome"], result["error_code"]))
        self.assertFalse(any(c["argv"][:1] == ["-p"] for c in self.calls()))  # the agent never started
        roots = self.real_writable_roots(str(ws))
        self.assertIn(Path("/tmp").resolve(), roots)  # Codex workspace-write can write /tmp and $TMPDIR

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
        self.assertEqual([], list((self.root / "evidence").rglob("agent-output")))
        self.fake("claude", json.dumps({**CLAUDE_OK, "result": "key sk-ant-" + "a" * 30}))
        backend = self.claude("x", env=self.env)
        result = self.run_backend(backend, self.request("claude", "sonnet", request_id="req-2"))
        self.assertEqual(("error", [], "agent-output"), (result["outcome"], result["artifacts"], backend.withheld))

    def test_each_provider_failure_signal_alone_is_an_error(self):
        cases = {"claude-is_error": ("claude", json.dumps({**CLAUDE_OK, "is_error": True})),
                 "claude-subtype": ("claude", json.dumps({**CLAUDE_OK, "subtype": "error_during_execution"})),
                 "codex-turn.failed": ("codex", json.dumps({"type": "turn.failed"}) + "\n" + json.dumps(CODEX_OK[-1])),
                 "codex-error": ("codex", json.dumps({"type": "error", "message": "x"}) + "\n" + json.dumps(CODEX_OK[-1]))}
        for i, (name, (provider, out)) in enumerate(cases.items()):
            with self.subTest(name):
                self.fake(provider, out)
                backend = (self.claude("x", env=self.env) if provider == "claude"
                           else execution.AgentCliBackend(provider, "x", env=self.env))
                result = self.run_backend(backend, self.request(provider, "m", request_id=f"fail-{i}"))
                self.assertEqual(("error", "PROVIDER_ERROR", "error"),
                                 (result["outcome"], result["error_code"], result["attempts"][0]["outcome"]))
                self.assertTrue(result["usage_events"])  # spent tokens are still reported

    def test_probe_errors_and_bad_diff_base_refuse(self):
        self.fake("codex", json.dumps(CODEX_OK[-1]))
        missing = execution.AgentCliBackend("codex", "x", env=self.env, sandbox_probe=["/nonexistent-probe"])
        result = self.run_backend(missing, self.request("codex", "m"))
        self.assertEqual("CAPABILITY_UNSUPPORTED", result["error_code"])
        self.assertIn("probe failed", missing.rejection_reasons["req-1"])
        self.git("init", "-q")
        (self.root / "workspace" / "a.txt").write_text("x\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        backend = self.claude("x", env=self.env, diff_base="0" * 40)  # not in the repository
        self.fake("claude", json.dumps(CLAUDE_OK))
        result = self.run_backend(backend, self.request("claude", "sonnet", request_id="r2"))
        self.assertEqual(("error", "LAUNCH_FAILED"), (result["outcome"], result["error_code"]))
        self.assertFalse(any(c["argv"][:1] == ["-p"] for c in self.calls()))

    def test_cancelled_diff_run_seals_no_candidate(self):
        self.git("init", "-q")
        (self.root / "workspace" / "a.txt").write_text("x\n")
        self.git("add", "."); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.fake("claude", json.dumps(CLAUDE_OK), sleep=30)
        launched = execution.launch(self.request("claude", "sonnet"),
                                    self.claude("x", env=self.env, diff_base=base, grace=0.5),
                                    workspace=str(self.root / "workspace"), evidence_root=str(self.root / "evidence"))
        for _ in range(100):
            if any(c["argv"][:1] == ["-p"] for c in self.calls()):
                break
            execution.time.sleep(0.05)
        launched.cancel()
        result = launched.result(30)
        self.assertEqual(("cancel", None), (result["outcome"], result["candidate"]))

    def test_large_prompt_to_a_cli_that_never_reads_still_times_out(self):
        script = self.root / "bin" / "claude"
        script.write_text("#!/bin/sh\n[ \"$1\" = --version ] && exit 0\nsleep 60\n")
        script.chmod(0o755)
        request = contract.validate_request({**self.request("claude", "sonnet"), "timeout_seconds": 2})
        started = execution.time.monotonic()
        result = self.run_backend(self.claude("p" * (2 << 20), env=self.env, grace=0.5), request)
        self.assertEqual("timeout", result["outcome"])
        self.assertLess(execution.time.monotonic() - started, 20)

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


class FakeOpenRouter:
    """An upstream for the relay: records what reaches it; answers a stream, or the daily-cap 429."""

    def __init__(self, test, status=200):
        self.seen, self.status = [], status
        upstream = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.seen.append((self.path, self.headers.get("Authorization"), body["model"]))
                if upstream.status == 429:
                    data = json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429,
                                                 "metadata": {"headers": {"X-RateLimit-Reset": "1791763200000"}}}})
                    self.send_response(429)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data.encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


def post(url, model, path="/api/v1/chat/completions"):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url + path, data=json.dumps({"model": model}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


OPENCODE_STEP = {"type": "step_finish", "part": {"type": "step-finish", "reason": "stop", "cost": 0,
                                                 "tokens": {"total": 115, "input": 95, "output": 10, "reasoning": 3,
                                                            "cache": {"write": 0, "read": 5}}}}


class OpenCodeTest(CliCase):
    MODEL = "cohere/north-mini-code:free"

    def key_file(self, mode=0o600):
        path = self.root / "openrouter.key"
        path.write_text("sk-or-v1-" + "k" * 40 + "\n")
        path.chmod(mode)
        return path

    def opencode(self, upstream, **kw):
        return execution.AgentCliBackend("opencode", "fix the bug", api_key_file=self.key_file(), upstream=upstream,
                                         sandbox_probe=["true"], env=self.env, **kw)

    def fake_opencode(self, upstream_url):
        """Runs inside the real sandbox: calls the relay once, tries every way out, prints OpenCode's JSON events."""
        script = self.root / "bin" / "opencode"
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, socket, sys, urllib.request
            prompt = sys.stdin.read()
            config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
            base = config["provider"]["openrouter"]["options"]["baseURL"]
            probe = {{"argv": sys.argv[1:], "prompt": prompt, "config": config, "pwd": os.environ.get("PWD"),
                      "env": sorted(k for k in os.environ if "OPENROUTER" in k or k.startswith("ANTHROPIC")),
                      "xdg": os.environ.get("XDG_DATA_HOME", "")}}
            def attempt(name, action):
                try:
                    action()
                    probe[name] = "allowed"
                except Exception as error:
                    probe[name] = type(error).__name__
            def relay(model):
                request = urllib.request.Request(base + "/chat/completions", data=json.dumps({{"model": model}}).encode(),
                                                 method="POST", headers={{"Content-Type": "application/json"}})
                try:
                    return urllib.request.urlopen(request, timeout=10).status
                except urllib.error.HTTPError as error:
                    return error.code
            probe["relay"] = relay(next(iter(config["provider"]["openrouter"]["models"])))
            probe["relay_other_model"] = relay("openai/gpt-6")
            attempt("upstream_direct", lambda: urllib.request.urlopen({upstream_url!r}, timeout=3))
            attempt("dns", lambda: socket.gethostbyname("example.com"))
            attempt("key_file", lambda: open({str(self.root / "openrouter.key")!r}).read())
            attempt("write_evidence", lambda: open({str(self.root / "evidence" / "forged")!r}, "w").write("x"))
            attempt("write_workspace", lambda: open("probe-write", "w").write("x"))
            open("probe.json", "w").write(json.dumps(probe))
            if probe["relay"] == 402:
                print(json.dumps({{"type": "error", "error": {{"name": "APIError", "data": {{"message": "quota"}}}}}}))
                sys.exit(1)
            for _ in range(2):
                print(json.dumps({OPENCODE_STEP!r}))
            """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def probe(self):
        return json.loads((self.root / "workspace" / "probe.json").read_text())

    def test_relay_forwards_only_the_runs_free_model_with_the_key(self):
        upstream = FakeOpenRouter(self)
        relay = execution._OpenRouterRelay("sk-or-v1-secret", self.MODEL, upstream.url)
        self.addCleanup(relay.close)
        url = f"http://127.0.0.1:{relay.port}"
        self.assertEqual((200, b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'),
                         post(url, self.MODEL))
        self.assertEqual(403, post(url, "google/gemini-nano-banana-2.1")[0])  # OpenCode's default title model
        self.assertEqual(403, post(url, self.MODEL, "/api/v1/embeddings")[0])
        self.assertEqual([("/api/v1/chat/completions", "Bearer sk-or-v1-secret", self.MODEL)], upstream.seen)
        self.assertEqual(1, relay.requests)

    def test_daily_cap_stops_forwarding_and_reports_the_reset(self):
        upstream = FakeOpenRouter(self, status=429)
        relay = execution._OpenRouterRelay("sk-or-v1-secret", self.MODEL, upstream.url)
        self.addCleanup(relay.close)
        url = f"http://127.0.0.1:{relay.port}"
        self.assertEqual(402, post(url, self.MODEL)[0])  # OpenCode retries a 429, never a 402
        self.assertEqual(402, post(url, self.MODEL)[0])
        self.assertEqual(1, len(upstream.seen))
        self.assertEqual(("quota_exhausted", 1791763200), (relay.quota["kind"], relay.quota["resets_at"]))
        today = datetime.datetime.now(datetime.UTC).date()
        midnight = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), datetime.UTC)
        self.assertEqual(int(midnight.timestamp()), execution._openrouter_reset({}, b"{}"))  # no reset header

    def test_opencode_usage_sums_steps_and_counts_reasoning_as_output(self):
        data = (json.dumps(OPENCODE_STEP) + "\n" + json.dumps(OPENCODE_STEP)).encode()
        self.assertEqual(({"input_tokens": 190, "output_tokens": 26, "cache_read_tokens": 10,
                           "cache_write_tokens": 0}, False, None), execution._opencode_report(data))
        self.assertTrue(execution._opencode_report(data + b'\n{"type": "error"}')[1])
        self.assertEqual((None, True, None), execution._opencode_report(b'{"type": "text"}'))

    def test_opencode_refuses_paid_models_and_missing_or_shared_keys(self):
        self.fake_opencode("http://127.0.0.1:1")
        backend = self.opencode("http://127.0.0.1:1")
        roots = {"workspace": str(self.root / "workspace"), "evidence_root": str(self.root / "evidence")}
        for model in ("openai/gpt-6", "openrouter/" + self.MODEL):
            self.assertEqual("CAPABILITY_UNSUPPORTED", backend.refuse(self.request("opencode", model), roots))
        self.assertIsNone(backend.refuse(self.request("opencode", self.MODEL), roots))
        self.key_file(0o644)
        self.assertEqual("BACKEND_UNAVAILABLE", backend.refuse(self.request("opencode", self.MODEL), roots))
        no_key = execution.AgentCliBackend("opencode", "x", sandbox_probe=["true"], env=self.env)
        self.assertEqual("BACKEND_UNAVAILABLE", no_key.refuse(self.request("opencode", self.MODEL), roots))
        with unittest.mock.patch.object(execution.sys, "platform", "linux"):
            self.assertNotEqual(0, subprocess.run(execution._default_probe("opencode"), check=False,
                                                  stderr=subprocess.DEVNULL).returncode)

    @unittest.skipUnless(sys.platform == "darwin", "Seatbelt")
    def test_opencode_runs_sandboxed_behind_the_relay(self):
        upstream = FakeOpenRouter(self)
        self.fake_opencode(upstream.url)
        self.env["OPENROUTER_API_KEY"] = "sk-or-v1-from-env-" + "e" * 30
        result = self.run_backend(self.opencode(upstream.url), self.request("opencode", self.MODEL))
        self.assertEqual(("completed", 0), (result["outcome"], result["exit_code"]), result)
        probe = self.probe()
        self.assertEqual(["run", "--format", "json", "--pure", "--title", "req-1", "-m",
                          "openrouter/" + self.MODEL], probe["argv"])
        self.assertEqual("fix the bug", probe["prompt"])
        self.assertEqual(str((self.root / "workspace").resolve()), probe["pwd"])  # OpenCode's project directory
        self.assertEqual([], probe["env"])  # no key, no billing variable reaches the sandbox
        self.assertEqual("openrouter/" + self.MODEL, probe["config"]["small_model"])
        self.assertEqual((200, 403), (probe["relay"], probe["relay_other_model"]))
        self.assertEqual([("/api/v1/chat/completions", "Bearer sk-or-v1-" + "k" * 40, self.MODEL)], upstream.seen)
        for way_out in ("upstream_direct", "dns", "key_file", "write_evidence"):
            self.assertNotEqual("allowed", probe[way_out], way_out)
        self.assertEqual("allowed", probe["write_workspace"])
        self.assertFalse(Path(probe["xdg"]).exists())  # OpenCode's private state is removed
        self.assertEqual({"input_tokens": 190, "output_tokens": 26, "cache_read_tokens": 10,
                          "cache_write_tokens": 0}, result["usage_events"][0]["units"])
        self.assertEqual("separate", result["usage_events"][0]["cache_semantics"])

    @unittest.skipUnless(sys.platform == "darwin", "Seatbelt")
    def test_opencode_daily_cap_is_quota_exhausted_without_candidate(self):
        upstream = FakeOpenRouter(self, status=429)
        self.fake_opencode(upstream.url)
        backend = self.opencode(upstream.url, candidate="probe.json")
        result = self.run_backend(backend, self.request("opencode", self.MODEL))
        self.assertEqual(("error", "PROVIDER_ERROR", None), (result["outcome"], result["error_code"],
                                                             result["candidate"]))
        self.assertEqual(("quota_exhausted", 1791763200), (backend.error_details["req-1"]["kind"],
                                                          backend.error_details["req-1"]["resets_at"]))


if __name__ == "__main__":
    unittest.main()
