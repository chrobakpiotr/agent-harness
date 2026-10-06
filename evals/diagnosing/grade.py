"""Grade one diagnosing run: workspace + Claude stream-json transcript -> JSON verdict.

Checks (all must hold for PASS):
- fix: the hidden tests pass against the workspace `invoice.py`;
- regression: a new or changed `test_*.py` fails against the base `invoice.py` with assertion failures only
  (no errors) and passes against the fix;
- red_before_fix: a `python` command whose output, but not its own text, holds the symptom `1998` runs before
  the first change of `invoice.py`: an edit tool, a shell segment naming `invoice.py` that is not a reader
  (`cat`, `grep`, `sed -n`, `git diff` ...), a redirect into it, or a Git command that rewrites the tree;
- hypotheses: under a line naming hypotheses, at least 3 list items that each state a prediction or outcome
  (predict, if, cause, confirmed, ruled out, falsified). Ranking and quality are not graded; read the handoff.

These are heuristics over a transcript: a shell idiom not listed here can still hide an edit.
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
SOURCE = re.compile(r"(?<![\w-])invoice\.py(?![\w.])")
REDIRECT = re.compile(r">>?\s*\S*(?<![\w-])invoice\.py(?![\w.])")
READERS = {"cat", "head", "tail", "grep", "rg", "less", "wc", "diff", "ls", "nl", "file"}
GIT_READERS = {"diff", "show", "log", "status", "blame", "grep"}
GIT_REWRITE = re.compile(r"\bgit\s+(apply|am|stash|checkout|restore|reset|cherry-pick|merge|rebase|pull|switch)\b")
HYPOTHESIS_ITEM = re.compile(r"(?i)predict|\bif\b|\bcause|confirmed|ruled out|falsif")


def _unittest(src, tests):
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(src, pathlib.Path(tmp, "invoice.py"))
        for test in tests:
            shutil.copy(test, tmp)
        names = [pathlib.Path(t).stem for t in tests]
        return subprocess.run([sys.executable, "-m", "unittest", *names], cwd=tmp,
                              capture_output=True, text=True, timeout=120, check=False)


def _passes(src, tests):
    return _unittest(src, tests).returncode == 0


def _fails_on_assertions(src, tests):
    return re.search(r"^FAILED \(failures=\d+\)$", _unittest(src, tests).stderr, re.MULTILINE) is not None


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
    if name != "Bash":
        return False
    for segment in re.split(r"&&|\|\||[;\n|]", str(args.get("command", ""))):
        words = segment.split()
        if GIT_REWRITE.search(segment) or REDIRECT.search(segment):
            return True
        if not words or not SOURCE.search(segment):
            continue
        reader = (words[0] in READERS or (words[0] == "sed" and "-n" in words and "-i" not in words)
                  or (words[0] == "git" and len(words) > 1 and words[1] in GIT_READERS))
        if not reader:
            return True
    return False


def _is_red(name, args, out):
    command = str(args.get("command", ""))
    return name == "Bash" and SYMPTOM in out and SYMPTOM not in command and re.search(r"\bpython", command) is not None


def _hypothesis_count(text):
    """Most list items stating a prediction or outcome under any line naming hypotheses."""
    lines, best = text.splitlines(), 0
    for start, line in enumerate(lines):
        if not re.search(r"(?i)hypothes", line):
            continue
        items = []
        for following in lines[start + 1:]:
            if re.match(r"\s*(\d+[.)]|[-*])\s", following):
                items.append(following)
            elif following.strip() and following.startswith((" ", "\t")) and items:
                items[-1] += " " + following.strip()
            elif following.strip():
                break
        best = max(best, sum(1 for item in items if HYPOTHESIS_ITEM.search(item)))
    return best


def grade(workspace, transcript):
    workspace = pathlib.Path(workspace)
    src = workspace / "invoice.py"
    fix = src.is_file() and _passes(src, [HIDDEN / "test_hidden.py"])

    new_tests = [t for t in sorted(workspace.glob("test_*.py"))
                 if not (FIXTURE / t.name).is_file() or (FIXTURE / t.name).read_bytes() != t.read_bytes()]
    regression = (bool(new_tests) and fix and _passes(src, new_tests)
                  and _fails_on_assertions(FIXTURE / "invoice.py", new_tests))

    events = _events(transcript)
    first_edit = next((i for i, (n, a, _) in enumerate(events) if _is_source_edit(n, a)), len(events))
    red_before_fix = any(_is_red(*event) for event in events[:first_edit])

    handoff = workspace / "HANDOFF.md"
    text = handoff.read_text(encoding="utf-8") if handoff.is_file() else ""
    hypotheses = _hypothesis_count(text) >= 3

    checks = {"fix": fix, "regression": regression, "red_before_fix": red_before_fix, "hypotheses": hypotheses}
    return {**checks, "score": sum(checks.values()), "pass": all(checks.values())}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: grade.py <workspace> <transcript.jsonl>")
    print(json.dumps(grade(sys.argv[1], sys.argv[2]), sort_keys=True))
