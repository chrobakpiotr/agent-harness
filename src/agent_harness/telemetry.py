"""Structured provenance and usage telemetry for agentic SDD runs.

All data stays in ignored .agent-runs/. No prompt/secret content is copied into provenance.json;
raw provider stdout/stderr remain separate local artifacts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any


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
        if typ == 'thread.started' and isinstance(event.get('thread_id'), str):
            thread_id = event['thread_id']
        if typ in {'turn.completed', 'turn.failed'}:
            terminal_type = str(typ)
            if isinstance(event.get('usage'), dict):
                usage = dict(event['usage'])
    return {
        'thread_id': thread_id,
        'terminal_event': terminal_type,
        'event_count': event_count,
        'usage': usage,
        # Codex subscription/API pricing is not inferred locally from token counts.
        'cost_usd': None,
    }


def parse_claude_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    usage = envelope.get('usage') if isinstance(envelope.get('usage'), dict) else None
    return {
        'session_id': envelope.get('session_id') if isinstance(envelope.get('session_id'), str) else None,
        'duration_ms': envelope.get('duration_ms') if isinstance(envelope.get('duration_ms'), (int, float)) else None,
        'duration_api_ms': envelope.get('duration_api_ms') if isinstance(envelope.get('duration_api_ms'), (int, float)) else None,
        'num_turns': envelope.get('num_turns') if isinstance(envelope.get('num_turns'), int) else None,
        'usage': usage,
        'cost_usd': envelope.get('total_cost_usd') if isinstance(envelope.get('total_cost_usd'), (int, float)) else None,
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
        provider = str(doc.get('provider') or 'unknown')
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        status = str(doc.get('status') or 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
        duration = doc.get('duration_ms')
        if isinstance(duration, int):
            total_duration_ms += duration
        metadata = doc.get('provider_metadata')
        if isinstance(metadata, dict):
            cost = metadata.get('cost_usd')
            if isinstance(cost, (int, float)):
                total_cost += float(cost)
                known_cost_runs += 1
            usage = metadata.get('usage')
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if isinstance(value, int) and ('token' in key or key.endswith('_tokens')):
                        token_totals[key] = token_totals.get(key, 0) + value
    return {
        'runs': matched_runs,
        'providers': provider_counts,
        'statuses': status_counts,
        'duration_ms': total_duration_ms,
        'known_cost_runs': known_cost_runs,
        'known_cost_usd': round(total_cost, 6),
        'tokens': token_totals,
    }
