"""Grade one diagnosing run: workspace + Claude stream-json transcript -> JSON verdict.

Checks (all must hold for PASS):
- fix: the hidden tests pass against the workspace `invoice.py`;
- regression: a new or changed `test_*.py` fails against the base `invoice.py` and passes against the fix;
- red_before_fix: a command output shows the symptom (`1998`) before the first edit of `invoice.py`;
- hypotheses: `HANDOFF.md` has a line naming hypotheses followed by a list of at least 3 numbered items.
  Ranking and falsifiability are not graded; read the handoff for those.
"""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
HIDDEN = HERE / "hidden"
SYMPTOM = "1998"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# ponytail: a shell edit is recognised only by these idioms; extend if a run edits another way.
SHELL_EDIT = re.compile(r"(sed\s+-i|>\s*invoice\.py|tee\s+invoice\.py|perl\s+-pi)")


def _passes(src, tests):
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(src, pathlib.Path(tmp, "invoice.py"))
        for test in tests:
            shutil.copy(test, tmp)
        names = [pathlib.Path(t).stem for t in tests]
        return subprocess.run([sys.executable, "-m", "unittest", *names], cwd=tmp,
                              capture_output=True, timeout=120).returncode == 0


def _events(transcript):
    """Tool calls in order: (name, input, result_text)."""
    calls, results = [], {}
    for line in pathlib.Path(transcript).read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(block)
            elif block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
                results[block.get("tool_use_id")] = str(content)
    return [(c.get("name"), c.get("input") or {}, results.get(c.get("id"), "")) for c in calls]


def _is_source_edit(name, args):
    if name in EDIT_TOOLS:
        return pathlib.Path(str(args.get("file_path", ""))).name == "invoice.py"
    return name == "Bash" and bool(SHELL_EDIT.search(str(args.get("command", ""))))


def _hypothesis_count(text):
    """Numbered items directly under the first line that mentions hypotheses (wrapped lines are indented)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.search(r"(?i)hypothes", line)), len(lines))
    count = 0
    for line in lines[start + 1:]:
        if re.match(r"\s*\d+[.)]\s", line):
            count += 1
        elif line.strip() and not line.startswith((" ", "\t")):
            break
    return count


def grade(workspace, transcript):
    workspace = pathlib.Path(workspace)
    src = workspace / "invoice.py"
    fix = src.is_file() and _passes(src, [HIDDEN / "test_hidden.py"])

    new_tests = [t for t in sorted(workspace.glob("test_*.py"))
                 if not (FIXTURE / t.name).is_file() or (FIXTURE / t.name).read_bytes() != t.read_bytes()]
    regression = bool(new_tests) and fix and _passes(src, new_tests) and not _passes(FIXTURE / "invoice.py", new_tests)

    events = _events(transcript)
    first_edit = next((i for i, (n, a, _) in enumerate(events) if _is_source_edit(n, a)), len(events))
    red_before_fix = any(n == "Bash" and SYMPTOM in out for n, _, out in events[:first_edit])

    handoff = workspace / "HANDOFF.md"
    text = handoff.read_text(encoding="utf-8") if handoff.is_file() else ""
    hypotheses = _hypothesis_count(text) >= 3

    checks = {"fix": fix, "regression": regression, "red_before_fix": red_before_fix, "hypotheses": hypotheses}
    return {**checks, "score": sum(checks.values()), "pass": all(checks.values())}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: grade.py <workspace> <transcript.jsonl>")
    print(json.dumps(grade(sys.argv[1], sys.argv[2]), sort_keys=True))
