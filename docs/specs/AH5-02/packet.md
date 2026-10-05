# AH5-02 — Public execution contract and fixtures

- Status: accepted. agent-harness owns the shared execution contract; agent-benchmark states consumer needs.
- Source: `agent-harness-agent-plan-2026-10-05.md`, AH5-02; requirements from
  `agent-benchmark/docs/execution-port.md` (read-only; benchmark repo untouched).

## Allowed paths

`src/agent_harness/contract.py`, `src/agent_harness/contract_fixtures/`, `pyproject.toml`
(package data), `tests/test_contract.py`, `docs/adr/0002-execution-contract.md`,
`docs/specs/AH5-02/`, `AGENTS.md` (map line).

## Acceptance criteria

- AC1: wrong binding or version is rejected.
- AC2: a valid result cannot contain secrets beyond ID-shaped fields (closed schema, no free text).
- AC3: golden fixtures (success, fail, timeout, unknown terminal, missing qualification) ship in the
  wheel and validate; malformed inputs are rejected with bounded codes.
- AC4: benchmark can implement a fake against exactly this contract — needs the benchmark side
  (AB5-06); here only the fixtures and validators it would use.

## Evidence (2026-10-05)

- `scripts/check-wheel.sh` on CPython 3.13.16: 15/15 OK; on 3.9.6: 15 run, 1 skipped (boundary test).
  Fixtures loaded from the installed wheel via `importlib.resources`.
- AC1: `test_version_mismatch`, `test_binding_mismatch`. AC2: `test_no_free_text_fields`.
  AC3: `test_fixture_set_is_shipped_and_valid` + 7 malformed-input tests.
- Mutation (from source, 3.13): dropping the completion/outcome rule failed
  `test_three_states_stay_separate`; disabling the qualified-isolation binding failed
  `test_binding_mismatch`; disabling the repo `..` check failed `test_repo_context_is_explicit_and_absolute`.
- AC4: NOT RUN here — benchmark adapter is AB5-06 in agent-benchmark. ADR 0002 lists its delta.
- Fake/controlled/real: fixtures are fake (`isolation_level: fake` or rejected); nothing qualified.
