# Version 0.16.0 — feature documents and state I/O (AH5-14a)

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.16.0`; consumers pin the tag
  SHA.
- Scope: AH5-14a `agent_harness.feature` and `agent_harness.feature_state` (`docs/specs/AH5-14/packet.md`). The
  contract, `probe_digest`, the shipped reviewed reference and the supported API are unchanged. No behaviour change.

## Acceptance criteria

- AC1: version 0.16.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.16.0` pushed with `main`. A fresh install from the tag SHA reports 0.16.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-23)

- **Evaluation:** pass with amendments at `4a3219a`; amendments applied in `b086960` (`docs/specs/AH5-14/packet.md`).
- **Rehearsal** (`rehearsal-14a`, library `4a3219a`, Showcase `3649784`, its 0.15.0 adoption, base on its pin
  `717f4ba`): test results identical; CI identical after masking; rollback ok in all three directions.
- **Non-docs difference from the rehearsed `4a3219a`:** the `common_dir=` binding, the added tests and the version.
  Checked directly: equivalence on all Showcase features identical; `test_harness` 255/255 and the other affected
  suites pass through the shim with the candidate installed.
- **Consumers:** Showcase adopts with the `harness.py` shim (a behaviour-identical cutover). agent-benchmark does not
  use these modules; a re-pin is optional.
- **Wheel:** `scripts/check-wheel.sh` on `9c24492`: 576 tests OK, 2 skipped. Later commits change only `docs/`.
