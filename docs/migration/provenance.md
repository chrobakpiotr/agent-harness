# Provenance of extracted modules

Source: Showcase `tooling/agent-harness/` at `50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`
(unchanged in that directory through `52e1837`). Same licence (MIT) and copyright holder.
Until cutover (AH5-06) Showcase remains the implementation owner; changes go there first.

| Package module | Source path | Source blob | Changes from source |
|---|---|---|---|
| `agent_harness/machine_outcomes.py` | `machine_outcomes.py` | `4944d690` | none |
| `agent_harness/trust.py` | `trust.py` | `0c4ac157` | shebang and unused `import pathlib` removed |
| `agent_harness/telemetry.py` | `telemetry.py` | `01f76609` | provenance/usage part only: `_MANUAL_*`, `record_manual` and `main` (CLI) not moved — they need `verification.store` and the lifecycle module; shebang and the then-unused `re`, `stat` imports removed |
| `agent_harness/repository.py` | `verification/store.py` (`_git`, `_worktrees`, `resolve_control_root`) | `50c18f9` | `StoreError` → `RepositoryError` with the same codes; new `resolve_repo_context` builds a contract `RepoContext` (parity with the source resolver checked on a repo + linked worktree) |
| `agent_harness/verification/__init__.py` | `verification/__init__.py` | `9ca74f28` | none |
| `agent_harness/verification/model.py` | `verification/model.py` | `9200387e` | `from __future__ import annotations` (PEP 604 annotations on Python 3.9) |
| `agent_harness/verification/serialization.py` | `verification/serialization.py` | `ef36fd81` | `from trust import` → `from agent_harness.trust import` |
| `agent_harness/verification/profile.py` | `verification/profile.py` | `14ede4f5` | none |
| `agent_harness/verification/fingerprint.py` | `verification/fingerprint.py` | `a29a7df6` | `from __future__ import annotations`; `resolve_control_root` from `agent_harness.repository` |
| `agent_harness/verification/human_grants.py` | `verification/human_grants.py` | `f221ec36` | none |
| `agent_harness/verification/planner.py` | `verification/planner.py` | `b8038d0b` | none |
| `agent_harness/verification/candidate.py` | `verification/candidate.py` | `2b5fd4e4` | `resolve_control_root` from `agent_harness.repository`; `Path.stat(follow_symlinks=False)` → `Path.lstat()` (Python 3.9) |
| `agent_harness/verification/store.py` | `verification/store.py` | `597ace86` | `_git`/`_worktrees`/`resolve_control_root` body replaced by `agent_harness.repository`, `RepositoryError` re-raised as `StoreError` with the same code; no default issuer registry (`__file__`), missing registry fails `FAILURE_GRANT_ISSUER_UNAVAILABLE`; new `profile_root` parameter passed to `validate_plan_record` |
| `agent_harness/verification/authority.py` | `verification/authority.py` | `4ac59537` | no `import harness`: `publish_and_accept`, `resolve_accepted`, `resolve_execution`, `prepare_task_plan` take an injected `lifecycle` port (the Showcase `harness` module fits it); profile root is an explicit `profile_root` parameter instead of `__file__`; `prepare_task_plan` takes `profile_id` (no built-in `showcase`); `resolve_execution` guard unchanged |
| `agent_harness/schemas/verification-profile.schema.json` | `schemas/verification-profile.schema.json` | `375b2f5e` | none |

Check a module against its source:

```text
git -C <showcase> show 50c18f9:tooling/agent-harness/<source path> | diff - src/agent_harness/<module>
```

Ported tests: `test_trust` (all), `test_machine_outcomes` (2 of 3; the `verify.py` case moves with it),
`test_telemetry` (4 of 6; the two manual-evidence cases need the lifecycle module),
`test_verification_profile` (the `showcase.json` case uses an inline gate), `test_verification_candidate`,
`test_verification_store` (all), `test_verification_authority` (13 of 15: the executor case moves with 04b,
lifecycle acceptance stays with the Showcase lifecycle module; the lifecycle is a mock port and
`tests/fixtures/verification-profiles/showcase.json` is the source profile `5180d881` as test input).
`test_verification_completion_boundary` drives the Showcase lifecycle CLI and stays there.

Parity (AH5-04a-1): on one disposable repository Showcase `50c18f9` (Python 3.13) and the package
(Python 3.9 and 3.13) produce byte-identical plan records, with and without a task command, and the same
`invalid-verification-plan` refusal for a forged `plan_id`, a dropped obligation or unit, a changed origin
and a traversing `profile_id`.
