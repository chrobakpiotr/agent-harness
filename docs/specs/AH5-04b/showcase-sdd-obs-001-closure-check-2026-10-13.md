# Showcase SDD-OBS-001 closure check (2026-10-13)

- Scope: Harness verification of Showcase's master-plan handoff: T-001–T-009, T-900 and T-990 complete;
  T-990 checkpoint `4355733`, merged into `main` as `73e2965`. The verification is a read-only look at the
  repository and the lifecycle state, plus test runs on exports and clones.
- Verdict: **closure not confirmed.** The integrated `main` fails 39 of 920 tests in T-900's own pinned evaluation
  image. T-900's "920 tests, 0 failures" was run on an uncommitted composition, not on the committed tree. One
  Harness-assigned T-900 item is also missing.

## Confirmed

- **Lifecycle.** All 11 tasks are `completed` in epoch `showcase-recovery-20261011-a24b8ae`, generation 1, and every
  task checkpoint is an ancestor of T-990's `4355733`.
- **The T-900 evidence manifest.** 198 entries, none missing, no sha256 or size mismatch.
- **Harness item 2 (T-900 scope, `7c97a11`).** `test_verification_backend_qualification.py`'s discovery test now
  resolves through `resolve_executable` and asserts that `shutil.which` is not called.
- **Harness item 3, mostly.** `test_verification_lifecycle.py::test_signed_manual_registration_reaches_coverage_verification_without_stubs`
  drives a signed `record-manual` through the real lifecycle. It asserts that the generation is unchanged, there is
  exactly one ledger entry, and the coverage proof verifies. T-004's `test_harness.py` covers idempotent replay and
  stale-generation refusal at the API level, on real state.

## Findings

### F1. The integrated `main` fails 39 tests

Setup: T-900's image `showcase-t900-eval:pinned-20261013` (digest `bb7600c8…`, as in its report), a real `git clone`
at `main` `73e2965`, and the T-900 packet command `python3 -m unittest discover -s tooling/agent-harness/tests`.

Result: **920 tests, FAILED (failures=10, errors=29)**, 672 s.

| Module | Failing | Cause seen |
|---|---|---|
| `test_verification_phase_d.py` | 27 | the fixtures' `store.publish_execution_terminal(...)` raises `invalid-execution-terminal`, consistent with T-009's stricter terminal-authority binding |
| `test_verification_noncacheable.py` | 10 | under `discover`; standalone it also cannot import `test_verification_supervisor` |
| `test_verification_profile_applicability.py` | 2 | `AssertionError: Items in the second set but not the first` |

The T-990 checkpoint `4355733` fails the same modules: 78 tests, 10 failures and 29 errors, in the same image on a
clone. T-990 changes no tooling relative to T-900's checkpoint `e3238a3`.

**Why T-900 passed.** Its provenance (`verification/22-final-linux-discovery/source.txt`) reads: "Candidate
7de8e69… **plus recorded canonical-main overlays and T-900 test-only fixture corrections**", and "**Temporary**
test_harness.py fixture correction". The 920-OK run therefore tested a composition that is not committed anywhere.

**Why no builder caught it.** No task's packet runs these three modules. Their only owner is T-900 (through
`tests/**`), and only its `discover` command covers them. T-990's four packet commands do not include a full
`discover`.

### F2. Harness item 1 (T-900 scope) is missing

No test asserts `executable-search-path-excluded`, and no test covers an executable under a nested excluded
worktree. Mutation check on `73e2965`'s tree, in the sandbox, command, qualification, profile and authority test
modules: the baseline gives 93 OK. With the search-path exclusion disabled, 93 OK. With the resolved-executable
exclusion disabled, 93 OK. Both guards remain unprotected against regression. The production code is correct.

## Required before SDD-OBS-001 counts as closed

1. **Correct T-900's completion** through `correct-completion` (`COMPLETION_SEMANTICALLY_INVALID`), citing F1: the
   evaluated composition is not the committed tree, and the committed tree fails 39 tests.
2. **Repair T-900, inside its `tests/**` scope.**
   - Commit the fixture corrections that the evaluation applied only as overlays.
   - Fix or replace the 39 failing tests against the accepted behaviour. For `phase_d`, update the fixtures to
     T-009's terminal-authority binding.
   - Add the F2 guard tests.
   - If a failure turns out to be a production regression rather than a stale test, that is a finding against the
     owning builder task, fixed through that task's lifecycle.
   - Rerun `discover` on the **committed** candidate in the pinned image, with no overlays, and record the
     provenance.
3. **Re-gate T-990** on the repaired T-900 result. Its crosswalk depends on T-900. Then merge to `main` and confirm
   the Agentic SDD CI run on the pushed `main`.

Showcase's own scoping stands: master closure authorizes implementation and verification-contract refresh, the
canonical Design Gate remains not-pass, and no production-readiness claim is made.

## Review of the T-900 closure repair and the T-007 CI repair (2026-10-14)

Verdict: **accept both repairs.** F1 and F2 are resolved on the composed candidate. Complete both repairs through
the lifecycle, re-gate T-990, then integrate and confirm GitHub CI.

### T-900 repair `987fe13` (parent `e3238a3`, T-900 `running` under its completion correction)

- **Scope.** It changes 4 files, all test files within T-900's `tests/**`, with no production code:
  - `test_verification_phase_d.py`, whose fixtures now carry T-009's terminal authority;
  - `test_verification_noncacheable.py` and `test_verification_profile_applicability.py`;
  - the new `test_verification_t900_resolver_guards.py`.
- **No weakening.** No assertion is removed, and there is no skip or `expectedFailure` in the three repaired
  modules.
- **F2 resolved.** The two guard tests build their roots under `pathlib.Path.home()` and assert
  `^executable-search-path-excluded$` and `^executable-path-excluded$`. Harness mutated each guard on its own (not
  the shared overlap helper):
  - with only the search-directory exclusion disabled, exactly
    `test_declared_search_directory_inside_excluded_root_is_refused` fails;
  - with only the search-loop executable exclusion disabled, exactly
    `test_executable_nested_under_excluded_worktree_is_refused` fails.
- **Evidence.** `report.json`'s sha256 is `00921cee…`, as stated. The evidence directory
  `docs/specs/SDD-OBS-001/evidence/T-900-repair-2026-10-13/` is **untracked** in the T-900 worktree. Commit it
  (T-900's `allowed_paths` include `docs/specs/SDD-OBS-001/evidence/**`).

### T-007 CI repair (uncommitted diff in the T-007 worktree, sha256 `245b6e70…` verified)

- **Scope.** Only `.github/workflows/agentic-sdd.yml` and `tests/test_eval.py`, within T-007's `allowed_paths`.
- **The CI change.** The `harness-tests` and `behavioral-evaluations` jobs replace `actions/setup-python` with
  `container: python:3.13.16-slim@sha256:70729b46…`, install Git for the repository fixtures, and still install
  `tooling/agent-harness/requirements.txt` (cryptography 49.0.0 and `agent-harness[grants]` at `16bb938`). That
  matches the T-900 evaluator image's environment. `test_eval.py` asserts the pinned container, the absence of
  `setup-python` and the Git install for both jobs.

### The composed candidate, rebuilt by Harness

`7fe7877` is not reachable in the repository, so Harness rebuilt the composition:
`main` `73e2965` + merge of `987fe13` + the T-007 diff (sha256 `245b6e70…`). It changes 6 files against `main`, all
tests or workflow.

In the T-900 image (`bb7600c8…`), on a real clone:

| Check | Result |
|---|---|
| `discover -s tooling/agent-harness/tests` | **922 tests OK**, 0 failures (762 s) |
| `eval.py run --suite baseline` | exit 0 |
| `eval.py run --suite adversarial` | exit 0 |
| `eval.py run --suite shipping-preflight` | exit 0 |
| `harness.py validate-all docs/specs` | exit 0 |
| `verification_contract.py validate docs/specs/SDD-OBS-001` | OK |

### Remaining steps

1. Commit the T-900 repair evidence, then `complete-repair` T-900 and T-007 through the lifecycle, binding each
   repair's evidence. Name the composition by its inputs (`73e2965`, `987fe13` and T-007's committed checkpoint),
   because `7fe7877` is not preserved.
2. **Re-gate T-990.** It is still `completed` on the old T-900 result and crosswalk. Correct and repair it, or
   reopen it, under Showcase's rules, so that the human-ready gate rests on the repaired T-900.
3. Integrate into `main`, push only when authorized, and confirm the Agentic SDD CI run on the pushed `main`. That
   is the first time the new container jobs run on GitHub.
4. One informational note. Showcase pins `agent-harness` at v0.5.0 (`16bb938`). The latest release is v0.8.4.
   Upgrading is optional and would need its own qualification check.
