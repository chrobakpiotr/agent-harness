# Showcase T-002 follow-up: bind executable resolution in the shared verification core (2026-10-11)

- Scope: a follow-up task definition for Showcase SDD-OBS-001, by Harness. The analysis is a read-only reading of the
  T-003 worktree (`1508310` plus uncommitted `runner.py`/`test_runner.py`) and of the T-002 shared core it builds
  on.
- Decision: **T-003 stays open** until VC-016 is covered for both direct and runner verification, or the accepted
  contract is revised. The fix belongs in the T-002 shared core, so direct and runner verification resolve
  executables in one place. It does not belong in the runner.

## Why VC-016 is not covered

VC-016 says untrusted worktree, provider, tracker and runtime content cannot select control-plane execution code.
Its hint names "malicious cwd, environment and import/executable paths". The shared core resolves executables from
the inherited environment in four places:

1. **Backends.** `discover_backends`, `_codex_helper`, `_bubblewrap` and `_macos_sandbox_exec` find `codex`,
   `bwrap`, `sandbox-exec` and `docker` with `shutil.which` on the process `PATH`. A relative entry, an empty entry,
   `.` or a writable directory earlier in `PATH` selects a fake "sandbox", and that fake then runs the command without
   isolation. Qualification binds the resolved path, but it qualifies whatever `PATH` found, fake included.
2. **Commands.** `verification_command` allowlists bare names (`python3`, `python`, `npm`, `npx`, `true`, `git`,
   `docker`, `helm`, `terraform`). The sandbox wrapper then `execvp`s them with the inherited `PATH` (`_safe_env`
   passes `PATH` through) and `cwd=worktree`. So a relative or worktree `PATH` entry resolves `python3` to worktree
   content.
3. **Toolchain variables.** `_safe_env` passes `JAVA_HOME`, `JDK_HOME`, `SDKROOT` and `DEVELOPER_DIR` through
   unchecked. Each of them selects executables (`java`, `xcrun` tools).
4. **The runner's `verify.py` launch.** T-003's `run_verification` starts
   `[sys.executable, HERE/'verify.py', …]` with the inherited environment and `cwd=worktree`. `PYTHONPATH`,
   `PYTHONHOME`, `PYTHONSTARTUP` and the user site directory can therefore inject imports into the control plane.

T-003's tests cover none of these paths, and T-002's tests do not either.

## Follow-up task (T-002 shared core)

Allowed paths: `tooling/agent-harness/verification_sandbox.py`, `verification_command.py`,
`verification/executor.py`, `verification/supervisor.py` and their test modules. The runner and `verify.py` call
sites stay in T-003, which only switches to the shared function.

1. **One resolver.** A single function in the shared core resolves every executable the verifier launches: the
   backends, the allowlisted commands and the Python used for control-plane re-entry. A test asserts there is no
   other `shutil.which` or bare-name launch in the verification modules, `runner.py` or `verify.py`.
2. **A trusted search path, not `PATH`.** The resolver searches only absolute, canonical directories. These are a
   fixed system set (`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`) plus toolchain directories declared in the bound
   verification profile. It refuses:
   - relative or empty entries;
   - any directory inside a worktree, the control repository's task area, the sandbox home or `$TMPDIR`;
   - any directory, or any parent of it, that is writable by group or others.

   `JAVA_HOME`, `JDK_HOME`, `SDKROOT` and `DEVELOPER_DIR` are passed only when they are validated the same way, or
   when the profile binds them.
3. **Bind, then check.** At planning/admission, record each resolved executable as its canonical path and sha256 in
   the plan or qualification identity, so it becomes part of `backend_identity`/`policy_identity`. At launch,
   resolve again and compare. A mismatch fails closed with a distinct reason and requires re-qualification.
4. **Absolute `argv[0]` everywhere.** Backend argv and the command inside the sandbox are both launched by their
   absolute path, so no `execvp` lookup happens. The child's `PATH` is set to the trusted search path, so a tool's
   own sub-launches (`npm` → `node`, `git` → helpers) cannot reach worktree shims either.
   - `./gradlew` stays worktree-relative on purpose: it is part of the code under test and runs inside the sandbox.
     State this exception explicitly in the contract.
5. **Isolated Python re-entry.** Control-plane Python launches (runner → `verify.py`, supervisor re-entry) use the
   resolved interpreter with `-I` (no `PYTHONPATH`, `PYTHONHOME`, user site or script-directory surprises) and an
   explicit environment allowlist.
   - The interpreter must lie outside every worktree.
   - The child environment never carries `PYTHONPATH`, `PYTHONSTARTUP`, `NODE_OPTIONS`, `LD_PRELOAD`, `DYLD_*`,
     `BASH_ENV`, `ENV`, `GIT_EXEC_PATH` or `JAVA_TOOL_OPTIONS`. `_safe_env` already omits most of these; assert all
     of them in a test.

## VC-016 tests (direct and runner path, each)

- **Fake executables on `PATH`.** `PATH` starts with `.`, with a relative directory, or with a worktree directory
  holding fake `python3`, `git`, `sandbox-exec` and `codex` that each write a marker file. Verification uses the
  trusted binaries or is rejected before launch. No marker exists afterwards.
- **Executable changed after qualification.** A bound executable whose bytes change after qualification (a test copy
  in a profile directory) fails closed at launch.
- **Python injection.** `PYTHONPATH` and `PYTHONSTARTUP` pointing into the worktree, and a `sitecustomize.py` in the
  worktree, have no effect on `verify.py`: a marker proves the injected code never ran.
- **Toolchain variable into the worktree.** `JAVA_HOME` pointing into the worktree is rejected, or is ignored in
  favour of the profile binding.
- **Shadowing package.** `cwd=worktree` with a shadowing `verification/` package in the worktree: the control
  repository's modules are the ones imported.
- **Import graph.** The resolver is the only resolution site (requirement 1).

## Note on T-003's uncommitted timeout change

`run_verification` no longer kills the requester on timeout. It waits on `communicate()` with no bound, trusting the
shared engine to enforce the deadline and drain. If the engine hangs, the runner hangs too. Keep the reason (do not
strand a detached child), but bound the second wait (timeout plus a fixed grace). Past that bound, report `TIMEOUT`
with the drain unconfirmed and fence the worktree. That is, do not evaluate postconditions as if the run were
terminal.

## Sequence

1. Showcase registers this follow-up against T-002's shared core, and Harness reviews it against requirements 1–5
   and the tests above.
2. T-003 rebases on it. It switches the `verify.py` launch to the shared resolver with `-I`, adds the runner-path
   VC-016 tests and resolves the timeout note.
3. Only then can T-003 close. The alternative is a contract revision that explicitly narrows VC-016, accepted
   through the normal spec-change path.

## Registration route (2026-10-12)

**Use `replan-task` on T-003.** No new task, no reopen of T-002, no feature replacement, no `.agent-state` edit.

Why this route fits the accepted contract and the lifecycle:

- **VC-016 already belongs to T-003.** It is in T-003's acceptance criteria, next to VC-010, VC-015 and VC-017.
  T-002's acceptance criteria do not include it. So T-002's completed attempt met its own contract and stays
  untouched. The shared-core change is the work that T-003's existing criterion requires; only T-003's permitted
  surface was too narrow.
- **`replan-task` is the supported operation for this.** It takes a CAS-bound running task (T-003 is `running`,
  attempt 1) and a full in-feature proposal whose `allowed_paths` and `verification` may change. It validates:
  - the paths are safe;
  - the criteria exist in the accepted spec or verification contract;
  - the dependencies are planned tasks.

  The task-ID set and the DAG stay unchanged, and the new packet revision enters T-003's lineage through the normal
  publication path.
- **No parallel write collision.** The shared-core files are owned only by T-002 (an ancestor of T-003) and T-009
  (a descendant). `verify.py` is owned only by T-001 (an ancestor). The validator checks overlapping
  `allowed_paths` only between builders that can run in parallel, so widening T-003 is admissible. T-009 later
  builds on T-003's result.

Alternatives, rejected:
- **Reopen T-002.** T-002 did not fail its contract. `reopen` also refuses while a descendant (T-003) is running,
  and it would prune descendant workspaces, discarding T-003's attempt.
- **Standalone feature.** The DAG does not model cross-feature dependencies. T-003 could not bind the fix's
  checkpoint, and two lifecycles would edit the same files.
- **`reconcile-feature` with a new task ID.** It rejects changes to the task-ID set by design.

Proposal for `docs/specs/SDD-OBS-001/replan-proposals/T-003.json`. Fields not listed stay as they are now:

- `allowed_paths`: keep `runner.py` and `tests/test_runner.py`, and add:
  - `tooling/agent-harness/verification_sandbox.py`, `verification_command.py`, `verification/executor.py`,
    `verification/supervisor.py`, `verify.py`;
  - their test modules (`tests/test_verification_sandbox.py`, `test_verification_command.py`,
    `test_verification_executor.py`, `test_verification_supervisor.py`, and the `verify.py` tests).
- `verification`: keep the runner command, and add T-002's verification command unchanged, so the shared core
  stays green. If any `verify.py` tests exist, add their command too.
- `objective`: append requirements 1–5 above, in one sentence each.
- `test_seam`: append the VC-016 tests above. Each runs on the direct path and on the runner path, and must first
  fail on the current code (red-green-refactor).
- `acceptance_criteria`: unchanged. VC-016 and VC-017 are already listed.

Order:
1. Showcase writes the proposal.
2. Harness reviews it as a contract change before it is applied.
3. `replan-task T-003` runs with `--expected-status running` and the current feature generation, under the normal
   reason and operator attribution.
4. T-003 implements the change, and Harness reviews the result against requirements 1–5, the VC-016 tests and the
   bounded post-timeout wait.

Whether this replan uses any of T-003's retry budget is decided by Showcase's existing replan rules. It needs no
human grant beyond the usual operator attribution.
