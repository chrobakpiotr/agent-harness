# Harness review round 4 of Showcase SDD-OBS-001 T-009 attempt 2 (2026-10-10)

- Scope: read-only review of the T-009 worktree (`agent/SDD-OBS-001/T-009`, HEAD `d72ab99` with the whole T-009 change
  uncommitted, including the untracked `test_verification_lifecycle.py`) against packet `sha256:4bf89ee9…` and the
  round-3 conditions; clones with the identical tree.
- Verdict: **fail; do not complete.** One new high finding (cache inputs narrowed) and two round-3 conditions still
  open.

## Round-3 conditions

| Condition | Status | Evidence |
|---|---|---|
| Checkpoint proof: seal comparison | met | removing `seal.head_sha != reviewed_checkpoint …` fails the coverage tests |
| Checkpoint proof: commit-type check | met | removing `checkpoint_type != 'commit'` fails the coverage tests |
| Coverage lock outcome | **open** | replacing the coverage `lifecycle_state_lock` (`harness.py` ~2505) with `nullcontext` passes all 38 coverage, race and serialization tests (baseline 38 OK) |
| Abort `harness_invocation_upper_bound == 0` in `harness.py` | **open** | replacing it with `True` (`harness.py` ~1387) passes the abort/terminal tests, including the new `test_safe_prelaunch_abort_requires_zero_harness_invocation_bound`, which pins the store-side check |

## New finding: cache inputs narrowed (high)

Since `6857169`, `verification-profiles/showcase.json` narrows gate `inputs` (the cache key) and `applicability`:

- `showcase-gradle-build`: `apps/ecommerce/frontend/**` → `apps/ecommerce/frontend/src/**`. The frontend is a Gradle
  subproject (`settings.gradle`: `addSubproject('adapter:ecommerce-frontend', 'apps/ecommerce/frontend')`), and the
  backend build depends on `:adapter:ecommerce-frontend:processGeneratedResources` (`ecommerce.gradle:19, 28, 76`).
  Changes to the frontend's own build files, `package.json`, `package-lock.json` or `angular.json` affect the Gradle
  build but no longer change its fingerprint.
- `tooling/quality/**` → `**/*.gradle`, `**/*.gradle.kts`, `**/*.xml`, `spotless/importorder`. That drops
  `tooling/quality/spotless/xml.prefs`, which `spotless-xml.gradle:9` reads (`eclipseWtp('xml').configFile(...)`).
- The critical regressions list the adapter modules explicitly, so a new `modules/adapters/<x>` is no longer covered.

Any of these can make a cached PASS be reused after a change it depends on. Restore the broad inputs, or prove each
excluded path cannot affect the gate and add a profile test per exclusion. Narrowing `applicability` alone is a
separate, weaker concern.

## Note

The worktree's branch was reset to `d72ab99`, so all T-009 work is uncommitted and `test_verification_lifecycle.py`
is untracked; a `git diff`-based copy misses it. Make sure the lifecycle checkpoint includes it.
