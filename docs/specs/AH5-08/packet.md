# AH5-08 — Move the verification runtime

- Status: **accepted**. This is step 3 of moving the Showcase harness into the library, and the owner ordered it
  on 2026-10-18. 08a is in progress; 08b follows.
- Source: Showcase `49ab97f`. The moved paths are frozen, read-only here. ADR 0001: decision 1 (after a packet
  moves a module, the library owns it), decision 3 (explicit roots) and decision 4 (no consumer imports).
- Order: 08a and 08b each ship in their own release. Showcase adopts a release with its cutover (AH5-06b) or in a
  later re-pin.

## Findings (2026-10-18)

Status of each module at `49ab97f`:

| Module | Lines | Coupling to Showcase |
|---|---|---|
| `verification_sandbox.py` | 1401 | Lines 31–174 already moved (`agent_harness.executables`). Gradle environment in `_safe_env`: `GRADLE_USER_HOME`, `GRADLE_OPTS`, `AGENTIC_SDD_GRADLE_RO_DEP_CACHE`. `./gradlew` worktree wrapper in `build_plan`. The protected roots hard-code `tooling/agent-harness` and `docs/specs`. |
| `verification/workspace.py` | 220 | None, apart from the barrier text. No caller outside tests (diagnostic-only, ADR 0001). |
| `verification/supervisor.py` | 1102 | Gradle retry evidence (`_registered_gradle_retry_evidence`, `_gradle_user_home_has_init_scripts`). Imports `verification_command.CommandResult`. |
| `verification_command.py` | 352 | Consumer policy: an allowlist of `./gradlew`, `python3`, `npm` and others, `PROFILE_SCRIPTS`, and `executable_binding_for_command`. `CommandExecutionBackend` and `run_command` are generic. |
| `verification/executor.py` | 504 | Calls `harness.load_validated`, `load_state`, `admit_verification_execution`, `consume_verification_launch` and `terminalize_verification_execution`. Imports `verification_command` and `machine_outcomes`. |

Callers outside the moved set use these sandbox names: `_safe_env`, `build_plan`, `ExecutionIdentity`,
`PreparedExecution`, `PosixProcessGroupBackend`, `BackendQualification`, `qualifies_v2`, `_sha256_file`, and the
executable names.

## AH5-08a — sandbox and workspace

- Allowed paths:
  - `src/agent_harness/sandbox.py`
  - `src/agent_harness/verification/workspace.py`
  - `tests/test_sandbox*.py`
  - `tests/test_verification_workspace.py`
  - `tests/test_backend_qualification.py`
  - `migration/showcase-wrapper/verification_sandbox.py`
  - `migration/showcase-wrapper/verification/workspace.py`
  - `migration/showcase-wrapper/tests/test_cutover_wrapper.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-08/`
  - `pyproject.toml`
  - `CHANGELOG.md`
- Seams. Each fails closed, is listed in the provenance doc and has a test. The Showcase shim supplies Showcase's
  policy, so Showcase behaviour is unchanged:
  1. **`environment_policy(env, sandbox_home)`**: adds consumer variables to the isolated environment. Its keys are
     also forwarded by bubblewrap `--setenv`. The library default adds nothing. The shim adds the Gradle variables
     and the read-only Gradle cache verbatim.
  2. **`worktree_wrappers`**: a mapping from a worktree-relative `argv[0]` to its error code. The wrapper is
     resolved inside the worktree with Showcase's regular-file, no-symlink and executable checks. The library
     default is empty, so a path-like `argv[0]` stays refused by `resolve_executable`. The shim passes
     `{'./gradlew': 'worktree-gradle-wrapper-invalid'}`.
  3. **`authority_roots`**: the paths, relative to the primary checkout, that a child may not write. The library
     default is `.agent-runs`, `.agent-state`, `docs/specs` and `.git`, plus the Git common directory. The shim
     adds `tooling/agent-harness`. Any ambiguity in resolving the primary checkout still returns `()`, and the
     strong backends refuse that.
- AC1: every other line keeps the `49ab97f` behaviour. The ported Showcase sandbox, workspace, doctor and
  backend-qualification tests pass in the library suite, except tests whose driver stays in Showcase; those are
  listed with the reason. Each seam has a test, and a mutation of each seam default fails its test.
- AC2: the cutover rehearsal at `49ab97f` with the new shims gives the same results as the AH5-06b-0 rehearsal.
  Every new difference is listed with its cause.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

## AH5-08b — supervisor, command backend and executor (later)

- Lifecycle port: `admit`, `consume` and `terminalize` join `load_validated`/`load_state`.
- A command-policy port carries `verification_command`'s allowlist and `PROFILE_SCRIPTS`.
- A retry-evidence port carries the Gradle retry evidence.
- Drafted after 08a is accepted.
