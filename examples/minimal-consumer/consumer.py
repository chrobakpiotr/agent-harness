"""Minimal consumer of agent-harness: public API only, runs from any directory against the installed package.

It builds its own request, launches it on the library's scripted (fake) backend, and keeps execution outcome,
harness completion and its own grade as three separate facts.
"""

import hashlib
import tempfile
from pathlib import Path

from agent_harness import contract, execution


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


request = contract.validate_request({
    "contract_version": contract.CONTRACT_VERSION, "request_id": "demo-1", "trial_id": None,
    "task_digest": digest("task"), "config_digest": digest("config"), "provider": "fake", "model": "fake-model",
    "settings": {}, "capabilities": ["usage"], "timeout_seconds": 60, "max_attempts": 1,
    "input_bindings": [{"name": "task", "sha256": digest("task")}],
})
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp).resolve()
    for name in ("workspace", "authority", "evidence"):
        (root / name).mkdir()
    repo = contract.validate_repo_context({
        "contract_version": contract.CONTRACT_VERSION, "repo_id": "demo", "base_sha": "0" * 40,
        "workspace": str(root / "workspace"), "authority_root": str(root / "authority"),
        "evidence_root": str(root / "evidence")})
    units = {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0, "cache_write_tokens": 0}
    backend = execution.ScriptedBackend([{"outcome": "completed", "units": units}], candidate=b"patch")
    result = execution.launch(request, repo, backend).result(timeout=30)

    grade = "NOT_GRADED"  # owned by the consumer, never derived from outcome or completion
    print(f"outcome={result['outcome']} completion={result['completion']} grade={grade} "
          f"usage={result['usage_completeness']} isolation={result['isolation_level']} "
          f"candidate={result['candidate']['sha256'][:15]}")

    cancelled = execution.launch({**request, "request_id": "demo-2", "capabilities": ["cancel"]}, repo,
                                 execution.ProcessBackend(["sleep", "30"]))
    cancelled.cancel()
    stopped = cancelled.result(timeout=30)
    print(f"cancel: outcome={stopped['outcome']} drain={stopped['drain']} isolation={stopped['isolation_level']}")
