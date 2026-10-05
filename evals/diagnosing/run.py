"""Live A/B run of the diagnosing practice (manual, never CI: it calls a model and costs money).

usage: python3 run.py [N]   -> .agent-runs/evals/diagnosing/<UTC time>/results.json
Arms differ only in the builder role: `practice` keeps its Practices pointers and ships the practice files,
`baseline` has the section removed. Claude runs isolated from user plugins, skills, hooks and CLAUDE.md.
"""

import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from grade import FIXTURE, grade

ROOT = pathlib.Path(__file__).resolve().parents[2]
SDD = ROOT / "docs" / "agentic-sdd"
PROMPT = "Read AGENTS.md and follow it."
AGENTS = """# Fixture repository

You are the builder: follow `docs/agentic-sdd/agents/builder.md`. The task packet is `ISSUE.md`;
allowed paths: `invoice.py`, `test_*.py`, `HANDOFF.md`. Verification: `python3 -m unittest`.
Finish by writing `HANDOFF.md` (constitution rule 16). No commits.
"""
CLAUDE = ["claude", "-p", PROMPT, "--safe-mode", "--setting-sources", "project", "--disable-slash-commands",
          "--no-session-persistence", "--output-format", "stream-json", "--verbose",
          "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash(python3:*),Bash(git:*),Bash(ls:*),Bash(cat:*)"]


def workspace(path, arm):
    shutil.copytree(FIXTURE, path)
    (path / "AGENTS.md").write_text(AGENTS)
    docs = path / "docs" / "agentic-sdd"
    (docs / "agents").mkdir(parents=True)
    shutil.copy(SDD / "constitution.md", docs)
    builder = (SDD / "agents" / "builder.md").read_text()
    if arm == "practice":
        shutil.copytree(SDD / "practices", docs / "practices")
    else:
        builder = re.sub(r"## Practices\n.*?(?=## )", "", builder, flags=re.DOTALL)
    (docs / "agents" / "builder.md").write_text(builder)
    git = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=path, check=True)


def main(runs):
    out = ROOT / ".agent-runs" / "evals" / "diagnosing" / datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    meta = {"repo_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "claude_version": subprocess.check_output(["claude", "--version"], text=True).strip(), "runs": runs}
    results = []
    for i in range(runs):
        for arm in ("baseline", "practice"):
            # Outside this checkout, so no parent AGENTS.md or settings reach the agent.
            ws, transcript = pathlib.Path(tempfile.mkdtemp()) / "ws", out / f"{arm}-{i}.jsonl"
            workspace(ws, arm)
            with transcript.open("w") as sink:
                code = subprocess.run(CLAUDE, cwd=ws, stdout=sink, stderr=subprocess.STDOUT, timeout=1800,
                                      check=False).returncode
            init = result = {}
            for line in transcript.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "system" and event.get("model"):
                    init = event
                if event.get("type") == "result":
                    result = event
            row = {"arm": arm, "run": i, "exit": code, "model": init.get("model"),
                   "cost_usd": result.get("total_cost_usd"), "turns": result.get("num_turns"), **grade(ws, transcript)}
            results.append(row)
            shutil.copytree(ws, out / f"{arm}-{i}", ignore=shutil.ignore_patterns(".git"))
            shutil.rmtree(ws.parent)
            print(json.dumps(row), flush=True)
    summary = {arm: {"pass": sum(r["pass"] for r in results if r["arm"] == arm),
                     "mean_score": sum(r["score"] for r in results if r["arm"] == arm) / runs} for arm in ("baseline", "practice")}
    (out / "results.json").write_text(json.dumps({**meta, "summary": summary, "results": results}, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
