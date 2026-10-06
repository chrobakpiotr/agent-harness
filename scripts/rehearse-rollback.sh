#!/usr/bin/env bash
# AH5-06a: state written by one side (wrapped/base) is read and continued by the other after swapping the
# harness files, on a minimal disposable repository. Needs the WORK dir of scripts/rehearse-cutover.sh.
# An accepted plan is bound to the exact candidate tree, so swapping code invalidates in-flight plans: after a
# real swap full reconstruction must refuse it, while the stored record and the lifecycle stay usable.
# usage: scripts/rehearse-rollback.sh <work dir>
set -euo pipefail
work=${1:?usage: rehearse-rollback.sh <work dir of rehearse-cutover.sh>}
feature=docs/specs/DEMO-002
# Sealing walks ignored files and refuses binary ones, so no bytecode caches.
export PYTHONDONTWRITEBYTECODE=1

cat > "$work/rb-write.py" <<'EOF'
import pathlib, subprocess, sys
sys.path.insert(0, 'tooling/agent-harness')
from verification import authority
base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
print(authority.prepare_task_plan(pathlib.Path('.'), pathlib.Path(sys.argv[1]), 'T-001', 1, base,
                                  ['python3 -V'])['plan_id'])
EOF
cat > "$work/rb-read.py" <<'EOF'
import pathlib, sys
sys.path.insert(0, 'tooling/agent-harness')
from verification import authority
from verification.store import StoreError, VerificationStore
plan_id, swapped = sys.argv[1], sys.argv[2] == 'swapped'
stored = VerificationStore(pathlib.Path('.')).load_plan_record(plan_id)
try:
    accepted = authority.resolve_accepted(pathlib.Path('.').resolve(), plan_id)
except StoreError as error:
    assert swapped and str(error) == 'invalid-verification-plan', error
    print('stored plan readable; in-flight plan refused after the code swap (candidate changed)')
else:
    assert not swapped and accepted == stored
    print('stored and accepted plan readable', plan_id[:44])
EOF

harness_from() {  # copy one side's harness files (no tests) into the repo
  rm -rf tooling/agent-harness
  mkdir -p tooling
  cp -R "$work/$1/tooling/agent-harness" tooling/
  rm -rf tooling/agent-harness/{tests,benchmarks,evals}
  find tooling -name __pycache__ -prune -exec rm -rf {} +
}

for pair in "wrapped base" "base wrapped" "base base"; do
  set -- $pair
  repo="$work/rollback-$1-to-$2"
  rm -rf "$repo" && mkdir -p "$repo/docs/specs" "$repo/docs/agentic-sdd" "$repo/.claude"
  cd "$repo"
  harness_from "$1"
  cp -R "$work/$1/$feature" docs/specs/
  cp -R "$work/$1/docs/agentic-sdd/agents" docs/agentic-sdd/
  cp -R "$work/$1/.claude/agents" .claude/
  cp "$work/$1/AGENTS.md" "$work/$1/.gitignore" .
  git init -q && git add -A
  git -c user.name=rehearsal -c user.email=rehearsal@example.invalid commit -qm disposable
  "$work/$1-venv/bin/python" tooling/agent-harness/harness.py claim "$feature" T-001 --owner rehearsal
  plan=$("$work/$1-venv/bin/python" "$work/rb-write.py" "$feature")
  harness_from "$2"
  if [ "$1" = "$2" ]; then swap=same; else swap=swapped; fi
  "$work/$2-venv/bin/python" "$work/rb-read.py" "$plan" "$swap"
  "$work/$2-venv/bin/python" tooling/agent-harness/harness.py heartbeat "$feature" T-001 --owner rehearsal
  echo "rollback $1 -> $2: ok"
done
