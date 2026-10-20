# AH5-13 — Move the verification-contract validation (`verification_contract.py`)

- Status: **implemented on branch `ah5-13-verification-contract`**; independent evaluation passed with amendments
  (applied, below); rehearsal pending.
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

Added: 12 tests, 5 for the seams and staleness (the constitution root and path; changed inputs; missing or empty
inputs; an unreadable input; requiredness through `is_active` and `require=`) and 7 for further rules. Callers in Showcase, unchanged: `harness.py` (`validate_contract`, `criterion_ids`), `wayfinder.py`
(`validate_contract(require=True)`).

## Seams (explicit, none built in)

| Seam | Parameter | Meaning | Showcase binding (looked up per call) |
|---|---|---|---|
| Repository root | `repo_root` (callable) | returns the repository root once the contract exists; the constitution is read under it | `lambda: repo_root()`: Showcase's own `repo_root`, its checkout (`REPO`) then the working directory's |
| Constitution directory | `repository` (path) | where the constitution is read from before the contract exists | `REPO` (`tooling/agent-harness/../..`), read at call time, so `vc.REPO` patches reach it |
| Constitution path | `constitution` (relative path) | the constitution's path under the root | `CONSTITUTION = 'docs/agentic-sdd/constitution.md'` |
| Active-feature layout | `is_active` (callable) | whether a feature directory is active, so `required` applies | `lambda path: is_active_feature_dir(path)`: `docs/specs/<feature>` |
All four are required keyword arguments: a call without them is a TypeError, never a default.

## Behaviour changes

None. The diff from the source, in five hunks: the two seam signatures, the `constitution` → `constitution_path`
local rename, `is_active(...)` for `is_active_feature_dir(...)`, and the seam-passing `input_hashes(...)` call; the
module docstring and the imports are the library's (`provenance.md`). The feature-internal paths (`spec.md`,
`plan.md`, `design.json`, `design/gate.json`, `design/master-closure.json`, `verification-contract.json`) are the
contract's own layout and stay verbatim; AH5-14a defines the feature-layout seam.

## Coverage (mutations on the library module)

A selection of 22 mutations, one per named rule; it is not a claim of full mutation coverage of the module. The first
16 were run at `e4322be`; the last 6 survived that suite in the independent evaluation and are caught since its
amendments.

| Mutation | Showcase's 4 tests | Caught by |
|---|---|---|
| schema_version unchecked | passes (gap) | `test_document_level_rules` |
| feature name unchecked | passes (gap) | `test_document_level_rules` |
| status unchecked | passes (gap) | `test_document_level_rules` |
| design gate not hashed | passes (gap) | `test_a_changed_constitution_or_design_record_makes_the_contract_stale` |
| master closure not hashed | passes (gap) | `test_a_changed_constitution_or_design_record_makes_the_contract_stale` |
| constitution not hashed | passes (gap) | `test_a_changed_constitution_or_design_record_makes_the_contract_stale`, `test_the_constitution_comes_from_the_bound_root_and_path` |
| constitution root ignored | passes (gap) | `test_the_constitution_comes_from_the_bound_root_and_path` |
| duplicate ids allowed | passes (gap) | `test_document_level_rules` |
| no independent criterion required | passes (gap) | `test_document_level_rules` |
| source_type unchecked | passes (gap) | `test_each_criterion_field_is_checked` |
| blank sources allowed | passes (gap) | `test_each_criterion_field_is_checked` |
| proposed exemption allowed | passes (gap) | `test_exemptions_need_a_unique_id_a_final_status_and_a_human_approver` |
| exemption id unchecked | passes (gap) | `test_exemptions_need_a_unique_id_a_final_status_and_a_human_approver` |
| approver prefix loosened (`human` for `human:`) | passes (gap) | `test_exemptions_need_a_unique_id_a_final_status_and_a_human_approver` |
| invalid requirement allowed | passes (gap) | `test_document_level_rules` |
| layout seam ignored | passes (gap) | `test_requiredness_follows_the_bound_layout_and_the_explicit_override` |
| spec not hashed | passes (gap) | `test_a_changed_constitution_or_design_record_makes_the_contract_stale`, `test_missing_or_empty_inputs_are_stale` |
| missing inputs accepted | passes (gap) | `test_missing_or_empty_inputs_are_stale` |
| independent criterion always required | passes (gap) | `test_requiredness_follows_the_bound_layout_and_the_explicit_override` |
| approver demanded for rejected exemptions | passes (gap) | `test_exemptions_need_a_unique_id_a_final_status_and_a_human_approver` |
| `criterion_ids` string filter removed | passes (gap) | `test_criterion_ids_keep_only_string_ids` |
| `die` exit code 2 → 1 | passes (gap) | `test_die_exits_with_code_2` |

## Open, recorded and not part of this packet

- **`generate` is a second launch path.** It builds its command with `runner.codex_command`/`claude_command` and
  runs it with `subprocess.run`, outside `AgentCliBackend` (master rule 7). None of `AgentCliBackend`'s guarantees
  apply to it:
  - the prompt is an argv element, not stdin (visible in process listings);
  - no `--preflight`, no isolation check, no evidence-root protection by real paths, no primary-checkout refusal;
  - the caller's API-key and token variables are inherited (no credential stripping, no `launch.json` login record);
  - no timeout and no cancel/drain; the result is waited for indefinitely;
  - stdout/stderr are written whole to `stdout.log`/`stderr.log`: no size limit, no credential-shaped-output withholding,
    no `withheld.json`;
  - no usage parsing (`usage_completeness`) and no contract result.
  What it does have: a fresh detached worktree, Codex `--sandbox read-only`, Claude's read-only tool set
  (`review_existing=True`), and the written contract is validated and removed if invalid. AH5-13 does not change it.
  Moving it onto the library's launch changes behaviour (prompt on stdin, preflight, evidence, withheld output), so it needs its own
  behaviour-change packet with per-row decisions, as AH5-12b did. Until then it stays Showcase-owned and unchanged.
- The shim changes `verification_contract.py`'s content, which is one of Showcase's protocol-fingerprint inputs
  (`harness.py`); the cutover changes that fingerprint, as earlier cutovers did. The moved logic stays covered:
  `requirements.txt`, which pins the library, is a fingerprint input too (`harness.py:231`).
- **Kept verbatim, recorded for a later rules decision (no rule changes in AH5-13):**
  - **Fail-open staleness:** if an input cannot be hashed (`OSError`, or `repo_root` dies), the whole staleness
    check is skipped and the contract can pass (`test_an_unreadable_input_skips_the_staleness_check_only` pins the
    source's behaviour). It conflicts in spirit with the plan's fail-closed rule.
  - **The validator against the contract schema** (`schemas/verification-contract.schema.json`, which describes the
    author's result): an exemption's `scope` and `reason` are required by the schema but not checked; criterion ids
    are checked with `startswith('VC-')`, the schema says `^VC-[0-9]{3}$`; `approved_by='human:'` with no name is
    accepted, the doc says `human:<name>`. Whitespace-only `statement`/`sources` are refused, stricter than the
    schema's `minLength 1`.
  - **Latent crashes:** a non-object `inputs`, or `criterion_ids` on a non-object contract, raises `AttributeError`.

## Consumer update (Showcase)

Copy `migration/showcase-wrapper/verification_contract.py` (mode 100755) and `tests/test_cutover_wrapper.py` at the
release SHA over `tooling/agent-harness/`, and move the version-pin tests. Showcase's own
`tests/test_verification_contract.py` stays and passes through the shim. Rollback: restore the previous pin and the
previous copies. agent-benchmark does not use this module.

## Independent evaluation (2026-10-20)

- **Verdict: pass with amendments**, at `e4322bea06f0acd806a2efc5ee62029c2492beb7` against Showcase `fd96eeb`. The
  code is correct: the library is verbatim apart from the seams, all four seams are required keyword arguments, the
  shim keeps `prompt`/`provider_args`/`generate`/`main`, `repo_root` and `is_active_feature_dir` byte-identical and
  looks `REPO` up per call, and the callers in `harness.py` and `wayfinder.py` keep working. Library 13/13, Showcase
  through the shim 30/30, `test_harness` contract-related 18/18.
- **Applied:**
  - 6 tests for the mutations that survived: spec changes make the contract stale; missing or empty `inputs` are
    stale; only a required contract needs an independent criterion; a rejected exemption needs no approver;
    `criterion_ids` keeps only string ids; `die` exits with 2. All 6 mutations are caught now.
  - The test exemptions carry the schema's `scope` and `reason`.
  - The test docstring and this packet say the mutation table is a selection; the coverage table maps each mutation
    to the test that catches it.
  - The diff description (five hunks) and the test split (4 ported, 12 added) are corrected.
  - The schema divergences, the fail-open staleness path and the latent crashes are recorded above, kept verbatim.
  - The fingerprint question is answered: `requirements.txt` is a fingerprint input.
  - The shim's extra blank line before `prompt` is removed.
