"""Minimal consumer of agent-harness: public API only, runs from any directory against the installed package.

It builds its own request, answers it with a consumer-side fake (the library has no launch API yet) and keeps
execution outcome, harness completion and its own grade as three separate facts.
"""

import hashlib

from agent_harness import contract


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


request = contract.validate_request({
    "contract_version": contract.CONTRACT_VERSION, "request_id": "demo-1", "trial_id": None,
    "task_digest": digest("task"), "config_digest": digest("config"), "provider": "fake", "model": "fake-model",
    "settings": {}, "capabilities": ["usage"], "timeout_seconds": 60, "max_attempts": 1,
    "input_bindings": [{"name": "task", "sha256": digest("task")}],
})
started, ended = "2026-10-05T12:00:00Z", "2026-10-05T12:00:01Z"
result = contract.validate_result({
    "contract_version": contract.CONTRACT_VERSION, "request_id": "demo-1",
    "request_digest": contract.request_digest(request), "execution_id": "exec-demo-1", "outcome": "completed",
    "exit_code": 0, "completion": None, "error_code": None, "drain": "confirmed", "cancel_requested": False,
    "isolation_level": "fake", "resolved_model": "fake-model", "versions": {}, "started_at": started,
    "ended_at": ended, "attempts": [{"attempt_id": "a1", "outcome": "completed", "started_at": started,
                                     "ended_at": ended}],
    "candidate": None, "artifacts": [], "usage_events": [], "usage_completeness": "unknown",
}, request)
grade = "NOT_GRADED"  # owned by the consumer, never derived from outcome or completion
print(f"outcome={result['outcome']} completion={result['completion']} grade={grade} "
      f"usage={result['usage_completeness']} isolation={result['isolation_level']}")
