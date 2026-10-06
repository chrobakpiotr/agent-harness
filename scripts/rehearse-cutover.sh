#!/usr/bin/env bash
# AH5-06a: rehearse the Showcase cutover on two exports (baseline, library wrapper); Showcase is never modified.
# usage: scripts/rehearse-cutover.sh <showcase checkout> [<sha>]   (WORK=<dir> keeps the results)
set -euo pipefail
export PIP_DISABLE_PIP_VERSION_CHECK=1

showcase=${1:?usage: rehearse-cutover.sh <showcase checkout> [<sha>]}
sha=${2:-HEAD}
repo=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3.13}
work=${WORK:-$(mktemp -d)}
mkdir -p "$work" && work=$(cd "$work" && pwd -P)  # resolved, as the harness prints paths
# Only read-only Git commands touch Showcase. Other work may move it meanwhile, so the check covers the harness.
harness_state() {
  git -C "$showcase" --no-optional-locks status --porcelain -- tooling/agent-harness
  git -C "$showcase" rev-parse "$sha"
}
before=$(harness_state)
head_before=$(git -C "$showcase" rev-parse --short HEAD)

"$python" -m venv "$work/build-venv"
"$work/build-venv/bin/pip" install --quiet build
"$work/build-venv/bin/python" -m build --wheel --outdir "$work/dist" "$repo" >/dev/null
wheel=$(ls "$work"/dist/agent_harness-*.whl)
if [ "$(uname -sm)" = "Darwin x86_64" ]; then crypto="cryptography<49"; else crypto="cryptography==49.0.0"; fi
# macOS /var -> /private/var: tests comparing resolved paths need a resolved temp root.
mkdir -p "$work/tmp" && export TMPDIR="$work/tmp"

# Every Python step of the Showcase agentic-sdd workflow at $sha except installs, the unit tests (run below)
# and report printing: `|` blocks hold one command per line, `>-` blocks fold into one command.
git -C "$showcase" show "$sha:.github/workflows/agentic-sdd.yml" | "$python" -c '
import re, sys
lines, i = sys.stdin.read().splitlines(), 0
while i < len(lines):
    m = re.match(r"(\s*)run: ?(.*)$", lines[i])
    i += 1
    if not m:
        continue
    head, block = m.group(2).strip(), []
    while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > len(m.group(1))):
        block.append(lines[i].strip()); i += 1
    commands = [" ".join(l for l in block if l)] if head == ">-" else [l for l in block if l] if head == "|" else [head]
    for c in commands:
        if c.startswith("python3 ") and "pip install" not in c and "unittest discover" not in c:
            print(c)
        else:
            print(c, file=sys.stderr)
' > "$work/ci-commands.txt" 2> "$work/ci-skipped.txt"

rehearse_side() {
  side=$1
  mkdir -p "$work/$side"
  git -C "$showcase" archive "$sha" | tar -x -C "$work/$side"
  if [ "$side" = wrapped ]; then cp -R "$repo/migration/showcase-wrapper/." "$work/$side/tooling/agent-harness/"; fi
  git -C "$work/$side" init -q
  git -C "$work/$side" add -A
  git -C "$work/$side" -c user.name=rehearsal -c user.email=rehearsal@example.invalid commit -qm export
  "$python" -m venv "$work/$side-venv"
  "$work/$side-venv/bin/pip" install --quiet --upgrade pip
  "$work/$side-venv/bin/pip" install --quiet --only-binary=cryptography "$crypto"
  if [ "$side" = wrapped ]; then "$work/$side-venv/bin/pip" install --quiet "$wheel"; fi
  py="$work/$side-venv/bin/python"
  # Tests start `python3` subprocesses; in Showcase CI that is the interpreter the requirements went into.
  export PATH="$work/$side-venv/bin:$PATH"
  # One process per test module, and per class for the large test_harness, several at a time: the suite
  # mostly waits on locks and subprocesses. Single-process `discover` puts the harness directory on sys.path
  # through earlier modules; here it is explicit, so modules that `import harness` run on their own.
  mkdir -p "$work/$side-logs"
  (cd "$work/$side/tooling/agent-harness/tests" && for module in test_*.py; do
     if [ "$module" = test_harness.py ]; then grep -oE '^class [A-Za-z0-9_]+' "$module" | sed 's/^class /test_harness./'
     else echo "${module%.py}"; fi
   done) | (cd "$work/$side" && xargs -P "${JOBS:-8}" -I{} sh -c \
     'PYTHONPATH=tooling/agent-harness/tests:tooling/agent-harness "$1" -m unittest -v "$2" > "$3/$2.log" 2>&1 || true' \
     _ "$py" {} "$work/$side-logs")
  cat "$work/$side-logs"/*.log > "$work/$side-tests.log"
  # "name (id) ... status"; a docstring moves " ... status" to the next line, printed output moves the status
  # to a later line of its own.
  awk 'function status(s) { if (s ~ /^skipped( |$)/) return "skipped"
                             if (s ~ /^(ok|FAIL|ERROR|expected failure|unexpected success)$/) return s; return "" }
       function emit(s) { if (s != "") { print name, s; name = "" } }
       /^test_[^ ]+ \([^ ]+\)( \.\.\. |$)/ { name = $1 " " $2; dots = index($0, " ... ")
                                         if (dots) emit(status(substr($0, dots + 5))); next }
       name != "" && index($0, " ... ") { emit(status(substr($0, index($0, " ... ") + 5))); next }
       name != "" { emit(status($0)) }' "$work/$side-tests.log" | sort > "$work/$side-results.txt"
  : > "$work/$side-ci.txt"
  : > "$work/$side-ci-rc.txt"
  while IFS= read -r command; do
    out=$(cd "$work/$side" && bash -c "$command" 2>&1) && rc=0 || rc=$?
    out=${out//$work\/$side-venv\//<venv>/}
    printf '## %s -> %s\n%s\n' "$command" "$rc" "${out//$work\/$side\//<export>/}" >> "$work/$side-ci.txt"
    printf '%s -> %s\n' "$command" "$rc" >> "$work/$side-ci-rc.txt"
  done < "$work/ci-commands.txt"
}

rehearse_side base & base_pid=$!
rehearse_side wrapped & wrapped_pid=$!
wait "$base_pid"
wait "$wrapped_pid"

[ "$before" = "$(harness_state)" ] && echo "showcase tooling/agent-harness unchanged: yes" \
  || echo "showcase tooling/agent-harness unchanged: NO"
echo "showcase HEAD during the run: $head_before -> $(git -C "$showcase" rev-parse --short HEAD)"
echo "tests base:    $(cut -d' ' -f3 "$work/base-results.txt" | sort | uniq -c | tr '\n' ' ')"
echo "tests wrapped: $(cut -d' ' -f3 "$work/wrapped-results.txt" | sort | uniq -c | tr '\n' ' ')"
diff "$work/base-results.txt" "$work/wrapped-results.txt" > "$work/tests.diff" && echo "test results: identical" \
  || echo "test results differ: $(grep -c '^[<>]' "$work/tests.diff") lines in $work/tests.diff"
echo "ci commands: $(wc -l < "$work/ci-commands.txt") from the Showcase workflow; skipped (installs, unit tests," \
     "report printing, non-Python): $(wc -l < "$work/ci-skipped.txt"), listed in $work/ci-skipped.txt"
diff "$work/base-ci-rc.txt" "$work/wrapped-ci-rc.txt" > "$work/ci-rc.diff" && echo "ci exit codes: identical" \
  || echo "ci exit codes differ: see $work/ci-rc.diff"
diff "$work/base-ci.txt" "$work/wrapped-ci.txt" > "$work/ci.diff" && echo "ci output: identical" \
  || echo "ci output differs ($(grep -c '^[<>]' "$work/ci.diff") lines, timings included): see $work/ci.diff"
# Durations, run ids and eval result directories vary per run; nothing else may differ.
mask() { sed -E 's/[0-9]+(\.[0-9]+)?/N/g; s/[0-9a-f]{12,}/H/g; s#evals/[a-z-]+/[A-Za-z0-9]+/#evals/X/#g' "$1"; }
diff <(mask "$work/base-ci.txt") <(mask "$work/wrapped-ci.txt") > "$work/ci-masked.diff" \
  && echo "ci output after masking numbers and ids: identical" \
  || echo "ci output after masking differs: see $work/ci-masked.diff"
echo "results: $work"
