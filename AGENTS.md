# agent-harness — Agent Operating Guide

Python library (`import agent_harness`) being extracted from the Showcase agent harness
(`tooling/agent-harness/` at Showcase SHA `50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`).
This file is a **map**, not a manual. Load only the context the task needs.

## Read order

1. `docs/agentic-sdd/constitution.md` — the shared agent contract (non-negotiable).
2. The accepted spec/packet of the task under `docs/specs/<task-id>/`.
3. Relevant ADRs under `docs/adr/` and migration notes under `docs/migration/`.
4. Only the source paths listed in the packet.

A small fix does not need Wayfinder ceremony.

## Map

- `src/agent_harness/` — the installable package. Importing it must have no side effects.
- `src/agent_harness/contract.py` — public execution contract v1 (owner of the shared schema; ADR 0002).
  It and the CLI are the only public API; every other module is internal (no compatibility promise).
- `src/agent_harness/{trust,telemetry,machine_outcomes}.py`, `verification/` — modules extracted from Showcase;
  provenance and allowed deviations in `docs/migration/provenance.md`.
- `tests/` — stdlib `unittest` suites; they run against the **installed wheel**, not the checkout.
- `examples/minimal-consumer/` — public-API-only consumer; `check-wheel.sh` runs it against the wheel.
  Consumer matrix: `docs/migration/consumers.md`.
- `scripts/check-wheel.sh` — build → fresh venv → smoke outside the checkout → tests. CI runs it.
- `docs/specs/` — task packets and evidence.
- `docs/agentic-sdd/agents/` — roles (`builder`, `evaluator`); each points at the practices it uses.
- `docs/agentic-sdd/practices/` — `diagnosing` (red gate or bug), `writing-for-agents` (editing an agent
  document), `retro` (human-invoked). Full description: `docs/agentic-sdd/handbook.md`.
- `migration/showcase-wrapper/`, `scripts/rehearse-{cutover,rollback}.sh` — Showcase cutover wrapper and its
  rehearsal (AH5-06); guide `docs/migration/cutover.md`.
- `evals/diagnosing/` — mini eval of the diagnosing practice; `run.py` calls a live model (manual only).

## Scope boundaries

- The library never imports a consumer (Showcase, benchmark).
- Application concerns (UI, AMQP poison gate, Redis gate, Order) never enter this repo.
- Before cutover Showcase stays the authoritative runtime source; do not keep two
  actively developed copies or two authority stores.
- Do not promote verification-v2 beyond its current state: origin admission and completion stay
  blocked, source workspace is diagnostic-only, Docker discovery is not qualification.
- No state, secrets, runtime logs or receipts in the package.

## Git mutation policy

- Do not push, force-push, merge, open a pull request, or mutate remotes unless a human explicitly requests it.
- Do not create or switch branches unless the active task protocol requires it.
- Commit locally only when a human explicitly asks or the accepted task protocol requires it.
- Never amend, squash, rewrite or delete history without authorization.
- Before an allowed commit run the relevant tests and `git diff --check`; afterwards report the SHA and tree status.

## Standard verification

```bash
scripts/check-wheel.sh
```
