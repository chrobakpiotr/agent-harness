# Showcase cutover guide (AH5-06)

For Showcase operators. Status: the cutover is **rehearsed** (AH5-06a); the real switch (AH5-06b) waits
for the qualified backend (AH5-04b/04c). Until then Showcase stays the implementation owner (ADR 0001).

## What changes

The modules already in the library stop being implemented in Showcase. Each Showcase file is replaced by
the wrapper in [`migration/showcase-wrapper/`](../../migration/showcase-wrapper/):

| Showcase file | After the cutover |
|---|---|
| `trust.py`, `machine_outcomes.py`, `verification/{model,serialization,profile,fingerprint,planner,candidate,human_grants}.py` | alias of the library module (same objects, so `isinstance` and test patches keep working) |
| `verification/store.py` | library store; binds `human-issuer-registry.json` and `verification-profiles/` |
| `verification/authority.py` | library authority; binds lifecycle `harness`, `verification-profiles/` and profile `showcase` |
| `telemetry.py` | library telemetry plus the Showcase-only manual evidence and CLI |

Everything else (`harness.py`, `orchestrate.py`, `runner.py`, `verify.py`, executor, supervisor, workspace,
sandbox, profiles, issuer registry, state under `.agent-state` and `.agent-runs/control`) stays in Showcase.
Origin admission and completion stay blocked; the qualified track stays NOT_QUALIFIED.

## Install and pin

1. In `tooling/agent-harness/requirements.txt` add the pinned library next to `cryptography`:
   `agent-harness[grants] @ git+https://github.com/chrobakpiotr/agent-harness.git@<tag SHA>` (a tag SHA,
   never a branch).
2. Copy the wrapper files (including `tests/test_cutover_wrapper.py`) over `tooling/agent-harness/`.
3. In the same change, remove only these five Showcase tests, which patch Showcase names that calls inside the
   library cannot see; their behaviour is tested in the library (`tests/test_verification_authority.py`) and
   the Showcase bindings by `test_cutover_wrapper.py`:
   `test_verification_authority.AcceptedPlanResolutionTest.` `test_exact_accepted_plan_reconstructs_current_units_without_running_them`,
   `test_origin_authority_is_v2_and_execution_fails_closed`, `test_post_seal_candidate_mutation_rejects_before_execution`,
   `test_trusted_orchestrator_plan_creation_binds_running_attempt_and_candidate`,
   `test_unknown_execution_unit_rejects_before_execution`. Every other Showcase test stays.
4. Add `tooling/agent-harness/requirements.txt` to `harness.protocol_files`: after the cutover `trust.py` and
   `telemetry.py` are wrappers, so the library pin must be part of the protocol fingerprint (a re-pin changes
   the trust classifier).
5. Run every step of the `agentic-sdd` workflow and the harness test suite.

## Boundaries

- Finish or release in-flight tasks first. An accepted verification plan is bound to the exact candidate tree;
  swapping the harness files (cutover or rollback) changes it, so full reconstruction refuses in-flight plans
  (`invalid-verification-plan`), exactly as any other code edit would. Stored records and lifecycle state stay
  readable and the lifecycle continues (`heartbeat`, `release`, `replan-task`).
- One writer per state root: never run the pre-cutover scripts and the wrapped ones against the same
  repository at the same time. The library writes no state at import.
- Changes to moved modules go to the library first, then Showcase re-pins.

## Rollback

Re-pin the previous library tag, or restore the pre-cutover files from Git
(`git checkout <pre-cutover SHA> -- tooling/agent-harness/`). The state format does not change, so state
written after the cutover is read by the restored scripts; `scripts/rehearse-cutover.sh` checks the code
paths, and the rollback rehearsal is recorded in `docs/specs/AH5-06/packet.md`.

## Known before the cutover (unchanged by it)

Candidate sealing (`prepare_task_plan`) refuses the full Showcase tree: it walks ignored files too, so a
secret-named path (`infra/k8s/helm/ecommerce/templates/secret.yaml`), high-entropy text, or binary `__pycache__`
files stop it (`SECRET_BEARING_CANDIDATE_UNSEALABLE`, `CANDIDATE_PRIVACY_PREFLIGHT_UNAVAILABLE`). Origin
admission is blocked anyway; the rehearsal uses a minimal repository with `PYTHONDONTWRITEBYTECODE=1`.

## Rehearse

`scripts/rehearse-cutover.sh <showcase checkout> [<sha>]` exports Showcase twice, applies the wrapper to one
copy, runs the harness tests and every Python step of the `agentic-sdd` workflow on both, and diffs them. It
only reads Showcase (`git archive`, `git --no-optional-locks status`, `rev-parse`).
`scripts/rehearse-rollback.sh <its WORK dir>` then writes lifecycle state and an accepted plan with one side and
reads and continues it with the other, in both directions.
