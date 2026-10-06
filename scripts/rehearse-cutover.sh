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
harness_state() { git -C "$showcase" status --porcelain -- tooling/agent-harness; git -C "$showcase" rev-parse "$sha"; }
before=$(harness_state)
head_before=$(git -C "$showcase" rev-parse --short HEAD)

"$python" -m venv "$work/build-venv"
"$work/build-venv/bin/pip" install --quiet build
"$work/build-venv/bin/python" -m build --wheel --outdir "$work/dist" "$repo" >/dev/null
wheel=$(ls "$work"/dist/agent_harness-*.whl)
if [ "$(uname -sm)" = "Darwin x86_64" ]; then crypto="cryptography<49"; else crypto="cryptography==49.0.0"; fi

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
  # mostly waits on locks and subprocesses.
  mkdir -p "$work/$side-logs"
  (cd "$work/$side/tooling/agent-harness/tests" && for module in test_*.py; do
     if [ "$module" = test_harness.py ]; then grep -oE '^class [A-Za-z0-9_]+' "$module" | sed 's/^class /test_harness./'
     else echo "${module%.py}"; fi
   done) | (cd "$work/$side" && xargs -P "${JOBS:-8}" -I{} sh -c \
     'PYTHONPATH=tooling/agent-harness/tests "$1" -m unittest -v "$2" > "$3/$2.log" 2>&1 || true' \
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
  for command in "harness.py doctor" "spec_inventory.py docs/specs --check docs/specs/INVENTORY.md" \
                 "harness.py validate-all docs/specs" "verification_contract.py validate docs/specs/SDD-001" \
                 "verification_contract.py validate docs/specs/SDD-OBS-001"; do
    # shellcheck disable=SC2086
    out=$(cd "$work/$side" && "$py" tooling/agent-harness/$command 2>&1) && rc=0 || rc=$?
    out=${out//$work\/$side-venv\//<venv>/}
    printf '## %s -> %s\n%s\n' "$command" "$rc" "${out//$work\/$side\//<export>/}" >> "$work/$side-ci.txt"
  done
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
diff "$work/base-ci.txt" "$work/wrapped-ci.txt" > "$work/ci.diff" && echo "ci commands: identical" \
  || echo "ci commands differ: see $work/ci.diff"
echo "results: $work"
