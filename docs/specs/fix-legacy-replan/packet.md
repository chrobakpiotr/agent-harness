# Legacy packet replan (narrow fix, behaviour change)

- Status: **candidate, for independent review** (branch `fix-legacy-replan`, from main). Authorized by master
  (2026-10-24) as a separate narrow fix; required for F1's adoption because the F1 recovery path runs `replan-task`.

## Defect

`packet_payload` writes `test_mode` and `test_seam` as `null` for a task without them, while the packet's
`semantic_contract_sha256` is computed from the planning task, where they are absent.
`packet_bound_semantic_contract_sha256` rebuilds the task from the packet's fields, including the `null`s, so the
hash no longer matches. `resolve_active_packet` reaches that check once a task has a non-legacy lineage, so the first
`replan-task` of such a task leaves an active packet history that `start` refuses with `ACTIVE_PACKET_AMBIGUOUS: packet
semantic contract fingerprint is inconsistent`. The harness thus produced packets that its own replan could not take.

## Fix

The existing, accepted legacy semantics: a task may omit `test_mode`/`test_seam` unless it is a builder under
`test_policy=risk-driven` (`feature.validate`, `replan-task`). Where the policy allows the omission, the reconstruction
also accepts the interpretation "stored `null` = absent" for exactly those two fields, as it already does for the
default `agent_profile`. The stored hash must still match one interpretation; nothing is invented, the validators are
unchanged, and a risk-driven builder's `null` stays inconsistent. Existing packets, which are immutable, are covered
without rewriting them.

## Verification

`tests/test_feature_transitions.py::LegacyPacketReplanTest`: a legacy builder, a legacy evaluator and a risk-driven
evaluator without the fields are accepted; a task with the fields is unchanged; a risk-driven builder with `null` and
a packet whose contract differs from its hash are still refused. Without the fix the first case fails (3 errors).
End to end: the F1 recovery test runs on tasks without the fields once both are combined (release candidate).
