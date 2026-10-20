# AH5-13 — Move the verification-contract validation (`verification_contract.py`)

- Status: **implemented on branch `ah5-13-verification-contract`**; rehearsal and independent evaluation pending.
  Release on the owner's order.
- Source: Showcase `tooling/agent-harness/verification_contract.py` at `fd96eeb` (352 lines; the file last changed at
  `486309b`) and `tests/test_verification_contract.py`.
- Freeze notice: from AH5-13's freeze notice, `verification_contract.py`'s validation part is library-owned, and
  Showcase's copy is frozen until the cutover replaces it with the shim (ADR 0001 amendment).

## Source and test map

| Part | Source functions | Destination |
|---|---|---|
| Validation | `VALID_*`, `die`, `sha`, `load_json`, `requirement`, `input_hashes`, `validate_contract`, `criterion_ids` | `agent_harness.verification_contract`, verbatim apart from the seams |
| Showcase policy | `repo_root` (this file's checkout, then the working directory's), `is_active_feature_dir` (the `docs/specs` layout), the constitution path `docs/agentic-sdd/constitution.md` | stays in the shim, bound per call |
| Authoring | `prompt`, `provider_args`, `generate`, `main` | stays in the shim, unchanged |

| Source test (`test_verification_contract.py`, 4) | Library test |
|---|---|
| `test_required_contract_must_exist` | same name |
| `test_valid_required_contract_has_independent_criterion` | same name |
| `test_contract_becomes_stale_when_plan_changes` | same name |
| `test_agent_cannot_self_accept_exemption` | same name |

Added: 2 seam tests (the constitution root and path; requiredness through `is_active` and `require=`) and 7 rule
tests. Callers in Showcase, unchanged: `harness.py` (`validate_contract`, `criterion_ids`), `wayfinder.py`
(`validate_contract(require=True)`).

## Seams (explicit, none built in)

- `input_hashes(feature_dir, *, repo_root, repository, constitution)`: `repo_root` is a callable (the source's
  `repo_root()`), `repository` the directory the constitution is read from before the contract exists (the source's
  `REPO`), `constitution` its relative path.
- `validate_contract(feature_dir, *, require=None, repo_root, repository, constitution, is_active)`: `is_active` is
  the source's `is_active_feature_dir`.
- The shim binds `repo_root`, `REPO`, the constitution path and `is_active_feature_dir`, looked up per call, so
  Showcase's tests that set `vc.REPO` still reach the library (`test_cutover_wrapper`).

## Behaviour changes

None. The diff from the source is the four seam lines (`provenance.md`). The feature-internal paths (`spec.md`,
`plan.md`, `design.json`, `design/gate.json`, `design/master-closure.json`, `verification-contract.json`) are the
contract's own layout and stay verbatim; AH5-14a defines the feature-layout seam.

## Coverage (mutations on the library module)

| Mutation | Showcase's 4 tests | Library suite (13) |
|---|---|---|
| schema_version unchecked | passes (gap) | caught |
| feature name unchecked | passes (gap) | caught |
| status unchecked | passes (gap) | caught |
| design gate not hashed | passes (gap) | caught |
| master closure not hashed | passes (gap) | caught |
| constitution not hashed | passes (gap) | caught |
| constitution root ignored | passes (gap) | caught |
| duplicate ids allowed | passes (gap) | caught |
| no independent criterion required | passes (gap) | caught |
| source_type unchecked | passes (gap) | caught |
| blank sources allowed | passes (gap) | caught |
| proposed exemption allowed | passes (gap) | caught |
| exemption id unchecked | passes (gap) | caught |
| approver prefix loosened (`human` for `human:`) | passes (gap) | caught |
| invalid requirement allowed | passes (gap) | caught |
| layout seam ignored | passes (gap) | caught |

## Open, recorded and not part of this packet

- **`generate` is a second launch path.** It still builds its command with the legacy `codex_command` /
  `claude_command` and runs it with `subprocess.run`, outside `AgentCliBackend` (master rule 7). Moving it onto the
  library's launch changes behaviour (prompt on stdin, preflight, evidence, withheld output), so it needs its own
  behaviour-change packet with per-row decisions, as AH5-12b did. Until then it stays Showcase-owned and unchanged.
- The shim changes `verification_contract.py`'s content, which is one of Showcase's protocol-fingerprint inputs
  (`harness.py`); the cutover changes that fingerprint, as earlier cutovers did.

## Consumer update (Showcase)

Copy `migration/showcase-wrapper/verification_contract.py` (mode 100755) and `tests/test_cutover_wrapper.py` at the
release SHA over `tooling/agent-harness/`, and move the version-pin tests. Showcase's own
`tests/test_verification_contract.py` stays and passes through the shim. Rollback: restore the previous pin and the
previous copies. agent-benchmark does not use this module.
