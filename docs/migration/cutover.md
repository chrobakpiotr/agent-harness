# Showcase cutover guide (AH5-06)

For Showcase operators. Here "shim" means the Python compatibility modules below; it is unrelated to the
S30 DR restore wrapper (inhibit, issuer acknowledgements, audited RESUME), which this cutover does not touch.

Status: the cutover is **rehearsed** (AH5-06a, AH5-06b-0, AH5-08 to AH5-10) and **ready**. It does not wait for
a qualified backend (ADR 0001, amendment 2026-10-18): the switch keeps every guard. Origin admission and
completion stay blocked, the qualified track stays NOT_QUALIFIED, and `doctor()` reports `qualified: False`.
Unblocking execution is a separate capability (AH5-04a-2, 04b, 04c), in its own packets.

## Ownership and frozen copies

A module has one implementation owner (ADR 0001, amendment 2026-10-18):
- Showcase, until the freeze notice of the packet that moves it;
- the library, from that notice on.

From the freeze notice, Showcase's file is a **frozen copy**. It is not edited until this cutover replaces it with
the shim, and every change goes to the library first.

| Showcase frozen copy | Library module | Frozen since | In release |
|---|---|---|---|
| `trust.py`, `machine_outcomes.py`, `telemetry.py`, `verification/{model,serialization,profile,fingerprint,planner,candidate,human_grants,store,authority}.py`, `schemas/verification-profile.schema.json` | `agent_harness.{trust,machine_outcomes,telemetry}`, `agent_harness.verification.*`, the profile schema | 2026-10-17 | 0.9.0 |
| `verification_sandbox.py`, `verification/workspace.py` | `agent_harness.{executables,sandbox}`, `agent_harness.verification.workspace` | 2026-10-18 | 0.10.0 |
| `verification/supervisor.py`, `verification/executor.py`, `verification_command.py` (generic part) | `agent_harness.verification.{supervisor,executor,command,retry}` | 2026-10-18 | 0.11.0 |
| `state_snapshots.py`, `eval.py` (engine) | `agent_harness.{state_snapshots,eval_runner}` | AH5-09/10 freeze notice | 0.12.0 |
| `verify.py` | `agent_harness.verify_cli` | AH5-11 freeze notice (2026-10-18) | 0.13.0 |
| `runner.py`, **partial**: the provider-neutral checks only (packet integrity, task-result schema projection and validation, git/path postconditions, `run_verification`) | `agent_harness.task_runner` | AH5-12a freeze notice (2026-10-18) | 0.13.0 |
| `runner.py`: the agent launch (provider argv, sandbox and isolation check, stdin prompt, timeout and cancel, evidence) | `agent_harness.execution.AgentCliBackend`, through the runner shim | AH5-12 freeze notice (2026-10-18) | 0.14.0 |
| `verification_contract.py`, **partial**: the validation (`requirement`, `input_hashes`, `validate_contract`, `criterion_ids`) | `agent_harness.verification_contract` | AH5-13 freeze notice (2026-10-20) | 0.15.0 |
| `harness.py`, **partial**: the feature documents and validation (`validate`, `load_validated`, fingerprints, design gate and closure checks), the state paths, identity checks and `save_state` | `agent_harness.feature`, `agent_harness.feature_state` | AH5-14a freeze notice (2026-10-23) | 0.16.0 |
| `harness.py`, **partial**: the lifecycle lock and state loading (`locked_state`, `lifecycle_state_lock`, `load_state`, …), the completion-record readers and the recovery checks on load | `agent_harness.feature_access`, with Showcase's recovery-epoch check as a refuse-only hook | AH5-14b-1 freeze notice (2026-10-24) | 0.17.0 |

Showcase policy stays Showcase-owned and is never frozen. The shims bind it, or it stays beside them:
- the Gradle environment, the `./gradlew` wrapper and the authority roots;
- the command allowlist and profile scripts;
- `verification/retry_controls.py`;
- `verification-profiles/`, `human-issuer-registry.json` and `evals/*.json`;
- the manual-evidence part of `telemetry.py`;
- `state_recovery.py`.

Not moved yet: `harness.py`, `orchestrate.py`, `design.py`, `wayfinder.py`, `verification_contract.py`, `control_plane.py`,
`spec_inventory.py`, and the rest of `runner.py`. The runner's provider launch, role prompts and projection profiles stay
Showcase-owned until AH5-12b; prompts and profiles stay Showcase policy. See `plan-remaining-2026-10-18.md`.

## What changes

The modules already in the library stop being implemented in Showcase. Each Showcase file is replaced by
the compatibility shim in [`migration/showcase-wrapper/`](../../migration/showcase-wrapper/):

| Showcase file | After the cutover |
|---|---|
| `trust.py`, `machine_outcomes.py`, `state_snapshots.py`, `verification/{model,serialization,profile,fingerprint,planner,candidate,human_grants,workspace,supervisor}.py` | alias of the library module. Imported by name it is the same object, so `isinstance` and test patches keep working. A test that loads the file by path gets a copy of the names, and patches on that copy do not reach the library: those tests are on the removal list |
| `verification/store.py` | library store; binds `human-issuer-registry.json`, `verification-profiles/` and the registered retry controls |
| `verification/retry_controls.py` (new) | Showcase's retry-control registry (the critical PostgreSQL and RabbitMQ Gradle gates), formerly in `verification/store.py` and `verification/supervisor.py` (AH5-08b) |
| `verification/executor.py` | library executor; binds lifecycle `harness`, the authority resolver, Showcase's store and command backend (AH5-08b) |
| `runner.py` | Showcase's runner. Its provider-neutral checks are the library's (`agent_harness.task_runner`): packet integrity, task-result schema projection and validation, git/path postconditions, the accepted-plan verification hook. Showcase binds its tested projection profiles, schema, `verify.py`, profiles and sandbox. The provider launch stays Showcase's until AH5-12b (AH5-12a) |
| `verify.py` | library verification CLI (`agent_harness.verify_cli`); binds `verification-profiles/` and Showcase's store, authority and executor (AH5-11) |
| `eval.py` | library eval engine; binds Showcase's suites (`evals/`, which stay Showcase-owned) and its repository (AH5-10) |
| `verification_command.py` | Showcase's allowlist, profile scripts and executable binding, over the library command backend and `run_command` (AH5-08b) |
| `verification/authority.py` | library authority; binds lifecycle `harness`, `verification-profiles/` and profile `showcase` |
| `telemetry.py` | library telemetry plus the Showcase-only manual evidence and CLI |
| `verification_sandbox.py` | library sandbox and executable resolver; binds the Gradle environment, the `./gradlew` worktree wrapper and the `tooling/agent-harness` authority root (AH5-08a) |

Everything else stays in Showcase: `harness.py`, `orchestrate.py`, the non-moved part of `runner.py` (above), profiles, the issuer registry, and state under `.agent-state` and `.agent-runs/control`.
Origin admission and completion stay blocked; the qualified track stays NOT_QUALIFIED.

## Install and pin

1. In `tooling/agent-harness/requirements.txt` add the pinned library next to `cryptography`:
   `agent-harness[grants] @ git+https://github.com/chrobakpiotr/agent-harness.git@<tag SHA>` (a tag SHA,
   never a branch).
2. Copy the wrapper files (including `tests/test_cutover_wrapper.py`) over `tooling/agent-harness/`. `test_cutover_wrapper.py` is library-owned: send new binding tests to Harness rather than editing your copy, so that a re-pin does not drop them (Showcase's eval-binding test is merged from `cbbdbca`).
3. In the same change, remove only the Showcase tests below. They patch Showcase names that calls inside
   the library cannot see. Each runs under the same name in the library's normal suite
   (`agent-harness/tests/test_verification_authority.py` and `test_verification_store.py`, run by
   `scripts/check-wheel.sh` and CI), and `test_cutover_wrapper.py` covers what Showcase passes in. Every other
   Showcase test stays.

   | Showcase `tooling/agent-harness/tests/test_verification_authority.py::AcceptedPlanResolutionTest` | Library assertions |
   |---|---|
   | `test_exact_accepted_plan_reconstructs_current_units_without_running_them` | identical (1) |
   | `test_origin_authority_is_v2_and_resolution_is_read_only` | identical (3) |
   | `test_post_seal_candidate_mutation_rejects_before_execution` | identical (1) |
   | `test_trusted_orchestrator_plan_creation_binds_running_attempt_and_candidate` | identical (6); the accept call additionally asserts `lifecycle=` and `profile_root=` |
   | `test_unknown_execution_unit_rejects_before_execution` | identical (1) |

   | Showcase `tooling/agent-harness/tests/test_verification_store.py::StoreTest` | Library assertions |
   |---|---|
   | `test_primary_repository_authority_resolution` | identical (5); patches `agent_harness.repository._worktrees`, and the store receives the registry as `issuer_registry_path` |

   AH5-08a (sandbox): these test the moved sandbox through its internals and run under the same names in the
   library's `test_sandbox.py`, `test_sandbox_doctor.py`, `test_backend_qualification.py` and
   `test_executables.py`. Their other tests stay and run through the shim, including the Gradle cache and
   `R14` (which drives `verification_command`).

   | Showcase test module | Tests to remove |
   |---|---|
   | `test_verification_sandbox_doctor.py` | its four tests; keep the module file, which the adversarial eval A007 runs by path and `test_eval.py` pins |
   | `test_verification_sandbox.py` | `test_auto_degrades_explicitly_when_platform_has_no_strong_backend`, `test_backend_discovery_uses_the_shared_resolver`, `test_bubblewrap_hides_verification_control_store_contents`, `test_bubblewrap_rebinds_trusted_issuer_registry_read_only`, `test_codex_sandbox_helper_uses_documented_command_shape`, `test_control_store_read_probe_fails_when_any_process_depth_can_read`, `test_required_fails_when_no_backend_exists`, `test_unsupported_backend_fails_closed_for_every_mode` |
   | `test_verification_backend_qualification.py` | `test_Q01_permitted_write_control_is_a_positive_probe`, `test_Q08_Q09_escape_evidence_rejects_otherwise_passing_candidate`, `test_R1_Q02_direct_write_bypass_is_detected_and_marker_cleaned`, `test_R1_R16_active_runner_returns_structured_evidence_for_all_checks`, `test_R2_Q03_child_write_bypass_is_detected_and_marker_cleaned`, `test_R3_Q04_grandchild_write_bypass_is_detected_and_marker_cleaned`, `test_R4_new_group_and_session_escapes_reject_process_group_backend`, `test_R5_parent_exit_with_live_descendant_is_not_drained`, `test_discovery_does_not_qualify_a_candidate`, `test_e33_containment_probe_kills_its_execution_unit`, `test_qualify_all_pass_is_all_or_nothing` |
   | `test_verification_t900_resolver_guards.py` | `test_executable_nested_under_excluded_worktree_is_refused` |
   | `test_state_snapshots.py` | `test_retention_keeps_current_and_first_snapshot_per_epoch` and `test_identical_full_tree_skips_snapshot_commit`. They assign `SNAPSHOT_LIMIT` or patch `_commit_snapshot` on the module they load by file path, which the library never sees; the second would pass vacuously (AH5-09) |

   Three Showcase tests are adapted instead of removed. Each patches a name that a library function looks up
   in its own module, so the patch target becomes the library module. Two are in `verification_command`'s tests.
   Change only the patch target, in
   `test_verification_command.py::test_resolved_plan_binding_is_forwarded_and_swap_fails_before_payload` (the
   patch around `VerificationSupervisor(store).execute(...)`, line 98 at `49ab97f`; leave the line-67 patch around
   `executable_binding_for_command` unchanged; line 162 in the second test) and in `::test_docker_discovery_alone_cannot_launch_in_auto_or_required_mode`:
   `mock.patch.object(command.verification_sandbox, 'resolve_executable', …)` becomes
   `mock.patch('agent_harness.sandbox.resolve_executable', …)`. With that change, all 11 tests pass through the shim.
   The third is in `test_verification_noncacheable.py::test_terminal_publication_crash_recovers_pass_without_relaunch`
   (AH5-08b): `mock.patch('verification.executor.VerificationSupervisor', …)` becomes
   `mock.patch('agent_harness.verification.executor.VerificationSupervisor', …)`. With that change, all 7 tests
   pass through the shims.

   The re-pin also moves the version that `test_qualification_report.py` asserts (`0.5.0` today) to the new tag;
   the rehearsal installs the working tree's version, so those three tests fail there by design.
4. Done in Showcase (`4fd9abe`, with a test): `tooling/agent-harness/requirements.txt` is in `harness.protocol_files`: after the cutover `trust.py` and
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
Showcase confirms this is intended fail-closed policy: clean generated caches before sealing, and any exception
for the templated Helm file needs its own narrow review and test, never a weaker general rule.

## Rehearse

`scripts/rehearse-cutover.sh <showcase checkout> [<sha>]` exports Showcase twice, applies the wrapper to one
copy, runs the harness tests and every Python step of the `agentic-sdd` workflow on both, and diffs them. It
only reads Showcase (`git archive`, `git --no-optional-locks status`, `rev-parse`).
`scripts/rehearse-rollback.sh <its WORK dir>` then writes lifecycle state and an accepted plan with one side and
reads and continues it with the other, in both directions.
