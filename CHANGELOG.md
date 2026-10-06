# Changelog

## 0.3.0 — 2026-10-06

- Contract v1 qualification report (`validate_qualification_report`, `qualification_passes`,
  `validate_capability_binding`, `verify_qualification_evidence`, `qualification_digest`) with mandatory checks
  Q01–Q16 and B1–B10, and CLI `agent-harness qualification --check` (AH5-04b-r).
- CLI `agent-harness constitution [--digest | --check PATH]`: the shared agent contract ships in the wheel;
  consumers check their verbatim copy for drift (AH5-00b). Constitution 1.0.1 (adds the check command to the
  ownership rule; no rule changes); a version names exactly one text.

## 0.2.0 — 2026-10-06

- **Breaking:** Python >= 3.13 (was >= 3.9). The `v0.1.0` tag keeps its 3.9 floor.
- Public offline launch/cancel API `agent_harness.execution` (ADR 0004): `launch`, `Execution`,
  `ScriptedBackend` (fake), `ProcessBackend` (controlled local process group); contract v1 unchanged.
- Optional extra `grants` (`cryptography==49.0.0`) for Ed25519 human retry grants; the core install keeps no
  third-party dependency.
- Internal (no compatibility promise): verification profile, fingerprint, planner, candidate, store,
  human grants and authority modules moved from Showcase `50c18f9` (AH5-04a-1). Plan acceptance goes through an
  injected lifecycle port; profile root and issuer registry are explicit parameters. Origin admission and
  completion stay blocked.
- Showcase cutover wrapper (`migration/showcase-wrapper/`), rehearsal scripts and operator guide (AH5-06a);
  Showcase itself is unchanged.
- Agent practices (`docs/agentic-sdd/`): `builder`/`evaluator` roles, `diagnosing`, `writing-for-agents`,
  `retro` adapted from mattpocock/skills, handbook, and the `evals/diagnosing` mini eval (AH5-07).

## 0.1.0 — 2026-10-05

- Execution contract v1 (`agent_harness.contract`): `validate_request`, `validate_result`,
  `validate_capability_report`, `validate_repo_context`, `request_digest`, `ContractError`.
- Golden fixtures in the wheel: success, fail, timeout, unknown-terminal, missing-qualification.
- `agent-harness --help/--version` CLI; no other commands.
- Not included: launch/cancel API, execution backends, Showcase runtime modules.
