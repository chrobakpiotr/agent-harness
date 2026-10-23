# Version 0.17.0 — lifecycle state access (AH5-14b-1)

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.17.0`; consumers pin the tag
  SHA.
- Scope: AH5-14b-1 `agent_harness.feature_access` (`docs/specs/AH5-14/packet.md`). The contract, `probe_digest`, the
  shipped reviewed reference and the supported API are unchanged. No behaviour change.

## Acceptance criteria

- AC1: version 0.17.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.17.0` pushed with `main`. A fresh install from the tag SHA reports 0.17.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-24)

- **Evaluation:** pass with amendments at `c3a5630`; the amendments (tests for four refusals, the incident hook
  documented, a corrected provenance claim) are applied in `df73de6`. They change only tests and docs: `src/` and
  `migration/` are identical to the rehearsed `c3a5630`.
- **Rehearsal** (`rehearsal-14b1`, library `c3a5630`, Showcase `1b22802d52f59c1629e20edbc7ec7aaf09761d79`, its
  0.16.0 adoption, base on its pin `fc4ad64`, `JOBS=4`): host window 2026-10-23T22:06:52Z to 22:48:54Z, load about
  3–9.
  - **CI:** 21 commands; exit codes identical; output identical after masking durations and eval ids.
  - **Tests:** base 921 ok, 2 FAIL, 1 ERROR; wrapped 922 ok, 1 FAIL, 1 ERROR. The only difference is
    `test_concurrent_sequential_replans_have_one_winner`, failing on the base side this time: the load- and
    timing-sensitive test recorded in every rehearsal. On both sides, the baseline failures
    `test_register_manual_observation_…` and `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`.
  - The version-pin tests did not differ because the rehearsed library still said 0.16.0; with 0.17.0 they are the
    expected difference and must pass after Showcase's re-pin.
  - **Rollback:** wrapped → base, base → wrapped and base → base all ok.
- **Open for master (not in this release):** whether the incident hook should also refuse a result that is neither
  `None` nor a non-empty dict (a behaviour change from the source). F1 stays a separate package; AH5-14c stays blocked.
- **Consumers:** Showcase adopts with the `harness.py` shim (behaviour-identical). agent-benchmark does not use this
  module.
