# Version 0.1.0 — execution contract library

- Status: accepted. Distribution: annotated Git tag `v0.1.0`; consumers pin the tag or its SHA.
  No GitHub Release and no PyPI package until AH5-06.
- Scope: the AH5-02 contract only. AH5-03a…AH5-06 (runtime extraction, qualified backend, cutover)
  are open; no launch API, nothing qualified.
- Open AH5-06 gates: independent evaluation (agent-benchmark consumer review of the contract is done)
  and the AH5-05 consumer matrix.

## Acceptance criteria

- AC1: version 0.1.0, MIT licence metadata, README with a runnable example, CHANGELOG.
- AC2: annotated tag `v0.1.0` pushed with `main`.
- AC3: a consumer installs `git+https://github.com/chrobakpiotr/agent-harness.git@v0.1.0` into a fresh
  venv and the README example runs from an empty directory.
