#!/usr/bin/env bash
# Build the wheel, install it into a fresh venv and test it outside the checkout.
set -euo pipefail
export PIP_DISABLE_PIP_VERSION_CHECK=1

repo=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

"$python" -m venv "$work/build-venv"
"$work/build-venv/bin/pip" install --quiet --upgrade pip build
"$work/build-venv/bin/python" -m build --wheel --outdir "$work/dist" "$repo"

"$python" -m venv "$work/venv"
"$work/venv/bin/pip" install --quiet "$work"/dist/agent_harness-*.whl

mkdir "$work/empty"
cd "$work/empty"
"$work/venv/bin/agent-harness" --help
"$work/venv/bin/python" -c '
import agent_harness, pathlib
assert "site-packages" in pathlib.Path(agent_harness.__file__).parts, agent_harness.__file__
print("installed:", agent_harness.__file__)'
"$work/venv/bin/python" -m unittest discover -s "$repo/tests" -v
