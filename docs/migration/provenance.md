# Provenance of extracted modules

Source: Showcase `tooling/agent-harness/` at `50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`
(unchanged in that directory through `52e1837`). Same licence (MIT) and copyright holder.
Until cutover (AH5-06) Showcase remains the implementation owner; changes go there first.

| Package module | Source path | Source blob | Changes from source |
|---|---|---|---|
| `agent_harness/machine_outcomes.py` | `machine_outcomes.py` | `4944d690` | none |
| `agent_harness/trust.py` | `trust.py` | `0c4ac157` | shebang and unused `import pathlib` removed |
| `agent_harness/telemetry.py` | `telemetry.py` | `01f76609` | provenance/usage part only: `_MANUAL_*`, `record_manual` and `main` (CLI) not moved — they need `verification.store` and the lifecycle module; shebang and the then-unused `re`, `stat` imports removed |
| `agent_harness/verification/__init__.py` | `verification/__init__.py` | `9ca74f28` | none |
| `agent_harness/verification/model.py` | `verification/model.py` | `9200387e` | `from __future__ import annotations` (PEP 604 annotations on Python 3.9) |
| `agent_harness/verification/serialization.py` | `verification/serialization.py` | `ef36fd81` | `from trust import` → `from agent_harness.trust import` |

Check a module against its source:

```text
git -C <showcase> show 50c18f9:tooling/agent-harness/<source path> | diff - src/agent_harness/<module>
```

Ported tests: `test_trust` (all), `test_machine_outcomes` (2 of 3; the `verify.py` case moves with it),
`test_telemetry` (4 of 6; the two manual-evidence cases move with `verification.store`).
