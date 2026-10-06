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
if [ "$(uname -sm)" = "Darwin x86_64" ]; then
  # cryptography 49.0.0 ships no Intel-macOS wheel; grant tests use the newest release that has one.
  "$work/venv/bin/pip" install --quiet --only-binary=cryptography "$wheel" "cryptography<49"
else
  "$work/venv/bin/pip" install --quiet --only-binary=cryptography "$wheel[grants]"
fi
# The grant tests skip without cryptography; fail here instead, and require the pin outside the fallback.
"$work/venv/bin/python" -c '
import cryptography, importlib.metadata, platform, re
pin = next(re.search(r"==([0-9.]+)", r).group(1)
           for r in importlib.metadata.requires("agent-harness") if r.startswith("cryptography"))
fallback = (platform.system(), platform.machine()) == ("Darwin", "x86_64")
assert fallback or cryptography.__version__ == pin, (cryptography.__version__, pin)
print("cryptography", cryptography.__version__, "pin", pin, "(Intel-macOS fallback)" if fallback else "")'

mkdir "$work/empty"
cd "$work/empty"
"$work/venv/bin/agent-harness" --help
"$work/venv/bin/python" -c '
import agent_harness, pathlib
assert "site-packages" in pathlib.Path(agent_harness.__file__).parts, agent_harness.__file__
print("installed:", agent_harness.__file__)'
"$work/venv/bin/python" -m unittest discover -s "$repo/tests" -v
"$work/venv/bin/python" "$repo/examples/minimal-consumer/consumer.py"
