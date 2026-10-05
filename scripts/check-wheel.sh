#!/usr/bin/env bash
# Build the wheel, install it into a fresh venv and test it outside the checkout.
set -euo pipefail
export PIP_DISABLE_PIP_VERSION_CHECK=1

repo=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3.13}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

"$python" -m venv "$work/build-venv"
"$work/build-venv/bin/pip" install --quiet --upgrade pip build
"$work/build-venv/bin/python" -m build --wheel --outdir "$work/dist" "$repo"

"$python" -m venv "$work/venv"
"$work/venv/bin/pip" install --quiet --upgrade pip  # old bundled pip misses cryptography wheels
wheel=$(ls "$work"/dist/agent_harness-*.whl)
# cryptography 47+ has no Intel-macOS wheel; there the grant tests run on the last release with one (46.x).
"$work/venv/bin/pip" install --quiet --only-binary=cryptography "$wheel[grants]" 2>/dev/null ||
  { "$work/venv/bin/pip" install --quiet --only-binary=cryptography "$wheel" "cryptography>=46,<47"
    echo "pinned cryptography has no wheel here; grant tests use $("$work/venv/bin/python" -c 'import cryptography; print(cryptography.__version__)')"; }

mkdir "$work/empty"
cd "$work/empty"
"$work/venv/bin/agent-harness" --help
"$work/venv/bin/python" -c '
import agent_harness, pathlib
assert "site-packages" in pathlib.Path(agent_harness.__file__).parts, agent_harness.__file__
print("installed:", agent_harness.__file__)'
"$work/venv/bin/python" -m unittest discover -s "$repo/tests" -v
"$work/venv/bin/python" "$repo/examples/minimal-consumer/consumer.py"
