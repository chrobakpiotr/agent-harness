"""Structured provenance and usage telemetry for agentic SDD runs.

All data stays in ignored .agent-runs/. No prompt/secret content is copied into provenance.json;
raw provider stdout/stderr remain separate local artifacts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import tempfile
from typing import Any

_TOKEN_FIELDS = {
    'input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens',
    'cache_creation_input_tokens', 'cache_read_input_tokens',
}
_PROVIDERS = {'codex', 'claude', 'manual'}
_STATUSES = {
    'pass', 'fail', 'provider-error', 'harness-error', 'running', 'abandoned',
    'needs-human', 'busy', 'stale-input', 'environment-blocked',
    'verification-blocked', 'verification-owned', 'invalid-policy',
    'invalid-cache', 'retry-policy-violation', 'verification-failed',
}
_SAFE_PROVIDER_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')
_MAX_SAFE_INTEGER = 2**63 - 1


def _safe_provider_id(value: Any) -> str | None:
    if not isinstance(value, str) or not _SAFE_PROVIDER_ID.fullmatch(value):
        return None
    from agent_harness.verification.serialization import default_safety
    return value if default_safety().safe(value) else None


def _safe_usage(value: Any) -> dict[str, int] | None:
    """Return only bounded numeric token counters from provider-controlled data."""
    if not isinstance(value, dict):
        return None
    clean: dict[str, int] = {}
    for key in _TOKEN_FIELDS:
        amount = value.get(key)
        if type(amount) is int and 0 <= amount <= 2**63 - 1:
            clean[key] = amount
    return clean or None


def _safe_cost(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        amount = float(value)
    except (OverflowError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def _safe_nonnegative_number(value: Any) -> int | float | None:
    """Keep finite nonnegative durations; bound integers before float conversion."""
    if type(value) is int:
        return value if 0 <= value <= _MAX_SAFE_INTEGER else None
    if type(value) is float and math.isfinite(value) and value >= 0:
        return value
    return None


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def sha256_file(path: pathlib.Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def parse_codex_jsonl(stdout: str) -> dict[str, Any]:
    """Extract stable, non-sensitive execution metadata from `codex exec --json` JSONL."""
    thread_id: str | None = None
    usage: dict[str, Any] | None = None
    terminal_type: str | None = None
    event_count = 0
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_count += 1
        typ = event.get('type')
        if typ == 'thread.started':
            thread_id = _safe_provider_id(event.get('thread_id'))
        if typ in {'turn.completed', 'turn.failed'}:
            terminal_type = str(typ)
            if isinstance(event.get('usage'), dict):
                usage = _safe_usage(event['usage'])
    return {
        'thread_id': thread_id,
        'terminal_event': terminal_type,
        'event_count': event_count,
        'usage': usage,
        # Codex subscription/API pricing is not inferred locally from token counts.
        'cost_usd': None,
    }


def parse_claude_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    usage = _safe_usage(envelope.get('usage'))
    cost = _safe_cost(envelope.get('total_cost_usd'))
    return {
        'session_id': _safe_provider_id(envelope.get('session_id')),
        'duration_ms': _safe_nonnegative_number(envelope.get('duration_ms')),
        'duration_api_ms': _safe_nonnegative_number(envelope.get('duration_api_ms')),
        'num_turns': envelope.get('num_turns') if type(envelope.get('num_turns')) is int and 0 <= envelope['num_turns'] <= _MAX_SAFE_INTEGER else None,
        'usage': usage,
        'cost_usd': cost,
    }


def reconcile_running(
    root: pathlib.Path, feature: str, orchestration_id: str, task_ids: set[str] | None = None,
    *, reason: str = 'orchestration no longer owns this invocation',
) -> list[str]:
    """Mark orphaned `running` provenance records as abandoned.

    This is used only after the orchestrator has deterministic evidence that ownership is gone
    (an expired lease was recovered) or the whole DAG has reached a terminal state. It never
    guesses liveness from wall-clock age alone.
    """
    base = root / '.agent-runs' / feature
    changed: list[str] = []
    if not base.exists():
        return changed
    for path in sorted(base.glob('*/**/provenance.json')):
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        if doc.get('orchestration_id') != orchestration_id or doc.get('status') != 'running':
            continue
        if task_ids is not None and str(doc.get('task')) not in task_ids:
            continue
        doc['status'] = 'abandoned'
        doc['completed_at'] = iso_now()
        doc['abandon_reason'] = reason
        atomic_write_json(path, doc)
        changed.append(str(path))
    return changed


def summarize(root: pathlib.Path, feature: str | None = None, orchestration_id: str | None = None) -> dict[str, Any]:
    base = root / '.agent-runs'
    if feature:
        base = base / feature
    manifests = list(base.rglob('provenance.json')) if base.exists() else []
    total_cost = 0.0
    matched_runs = 0
    known_cost_runs = 0
    unknown_cost_runs = 0
    known_usage_runs = 0
    unknown_usage_runs = 0
    total_duration_ms = 0
    provider_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    token_totals: dict[str, int] = {}
    for path in manifests:
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        if orchestration_id is not None and doc.get('orchestration_id') != orchestration_id:
            continue
        matched_runs += 1
        provider_value = doc.get('provider')
        provider = provider_value if isinstance(provider_value, str) and provider_value in _PROVIDERS else 'unknown'
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        status_value = doc.get('status')
        status = status_value if isinstance(status_value, str) and status_value in _STATUSES else 'unknown'
        status_counts[status] = status_counts.get(status, 0) + 1
        duration = doc.get('duration_ms')
        if type(duration) is int and 0 <= duration <= 2**63 - 1:
            total_duration_ms += duration
        metadata = doc.get('provider_metadata')
        if isinstance(metadata, dict):
            cost = _safe_cost(metadata.get('cost_usd'))
            if cost is not None and math.isfinite(total_cost + cost):
                total_cost += cost
                known_cost_runs += 1
            else:
                unknown_cost_runs += 1
            usage = _safe_usage(metadata.get('usage'))
            if usage:
                known_usage_runs += 1
                for key, value in usage.items():
                    token_totals[key] = token_totals.get(key, 0) + value
            else:
                unknown_usage_runs += 1
        else:
            unknown_cost_runs += 1
            unknown_usage_runs += 1
    return {
        'runs': matched_runs,
        'providers': provider_counts,
        'statuses': status_counts,
        'duration_ms': total_duration_ms,
        'known_cost_runs': known_cost_runs,
        'known_cost_usd': round(total_cost, 6) if known_cost_runs else None,
        'unknown_cost_runs': unknown_cost_runs,
        'known_usage_runs': known_usage_runs,
        'unknown_usage_runs': unknown_usage_runs,
        'tokens': token_totals,
    }
