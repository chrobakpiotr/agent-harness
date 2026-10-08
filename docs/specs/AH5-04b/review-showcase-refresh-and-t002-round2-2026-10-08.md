# Harness review round 2: Showcase worktree refresh `7417324` and T-002 `5de75f1` (2026-10-08)

- Scope: read-only; experiments in throwaway clones. Follows
  [`review-showcase-worktree-refresh-2026-10-08.md`](review-showcase-worktree-refresh-2026-10-08.md).

## T-002 completion blocker: not a validator bug

`harness.py packet docs/specs/AH5-04B-QUAL-001 T-002 --identity` (read-only):

| Run from | `harness.py` | Result |
|---|---|---|
| primary checkout (`main`, contains the `c86fd0a` merge) | accepts `expected_status` `failed` | revision `sha256:163acaae…7c`, exit 0 |
| T-002 worktree (based on T-001 checkpoint `218bfa1`) | predates `c86fd0a`, `running` only | `ACTIVE_PACKET_AMBIGUOUS`, exit 2 |

Lifecycle state is shared; the task worktree carries the dependency's older tooling, which the refresh deliberately
does not overwrite. Run lifecycle commands (`packet`, `complete`, …) with the primary checkout's `harness.py` against
the task worktree. No validator change is needed.

## 7417324 — worktree refresh: fail

Fixed from round 1: dependency output in the feature dir survives; no refresh commit when nothing is stale; the
snapshot is committed `HEAD` and dirty selected paths fail closed with cleanup; dead `agent-harness` removed and
`tooling/agent-harness/**` untouched.

| # | Severity | Finding |
|---|---|---|
| 1 | high | Regression: `current_execution_snapshot` now dies on dirty protocol/feature files for every `execution_base` caller (`design.py:237`, `wayfinder.py:677`, `:891`, `verification_contract.py:278`), not only task worktrees. `test_wayfinder.test_full_handoff_wayfinder_to_spec_design_to_tasks_with_fake_codex` fails at `7417324` and passes at `c6809f2` (reproduced). Out of the spec's scope ("changes only task-worktree preparation"). |
| 2 | medium | Protocol paths the dependency changed (`.gitignore`, `docs/agentic-sdd/**`) are reverted to root's version by a refresh commit even when the fingerprinted files are current; merging back undoes the dependency's work. Refresh only when the guarded set is stale, or state the revert in the spec. |
| 3 | medium | Still no non-mock test where a real refresh fails the real guard (removing the post-refresh guard call fails only the mock-based test). Real case: a fingerprinted `design.json` git-ignored in root. |
| 4 | low | Refreshed set (6 protocol entries plus 7 feature files) ≠ guarded set (`feature_fingerprint` over the 7 feature files; `protocol_version` is a constant). Pre-existing; dead synthetic-base references remain (`base_kind='synthetic-local'`, "harness can synthesize a local base"). |

Fix: keep `execution_base`'s previous snapshot behaviour for the design/wayfinder/verification-contract callers and
apply the committed-`HEAD` rule only inside `prepare_task_worktree`; refresh only when the guarded files differ; add
the real-guard test; remove the dead references; rerun `test_wayfinder`, `test_design`,
`test_verification_contract`, `test_orchestrate` and `test_harness`.

## 5de75f1 — T-002 probes: pass with two conditions

Q12 now advances the generation by CAS and refuses the previous one; Q15 SIGKILLs the controller after the active
identity is durable and recovers it from a fresh controller; Q16 SIGKILLs the drain controller after the tombstone is
durable, then a fresh controller's relaunch is refused. Six tests pass. Mutations: generation not advanced → caught;
SIGTERM instead of SIGKILL → caught.

1. Mutation `refused = relaunch_attempted` (relaunch refused without the drained tombstone) survives: add a test that
   a relaunch against an active or tombstone-less identity is not reported as refused.
2. Mutation `controller_killed: True` (kill outcome not checked) survives: add a test where the controller exits on
   its own before the kill and the probe fails.
3. Note: Q12 shows the stale generation is refused by the identity comparison; `cancel`/`drain` still take no caller
   generation. Acceptable for this qualification; a production execution backend must fence every operation by
   generation.

## Order

1. T-002: add the two tests in the running attempt, then complete with the primary checkout's `harness.py`.
2. Refresh fix round 3 (above); Harness reviews it.
3. Only then recreate T-003…T-900 worktrees, rerun, regenerate the report and get it independently reviewed.
