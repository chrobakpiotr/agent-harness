"""Validation of the independent verification contract (AH5-13).

Verbatim from Showcase `tooling/agent-harness/verification_contract.py` at `fd96eeb`: the contract's shape, its
staleness against the hashed feature inputs, its criteria and its human-only exemptions. The consumer binds its
policy explicitly; none is built in:

- ``repo_root``: a callable returning the repository root (the source's ``repo_root()``);
- ``repository``: the directory the constitution is read from when the contract does not exist yet (the source's
  ``REPO``);
- ``constitution``: the constitution's path relative to the root;
- ``is_active``: whether a feature directory is an active one (the source's ``docs/specs`` layout check).

Authoring a contract (the source's ``generate``) launches an agent and stays with the consumer.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any

VALID_REQUIREMENT = {'required', 'optional', 'off'}
VALID_ORIGINS = {'spec-derived', 'independent'}
VALID_SOURCE_TYPES = {'spec', 'adr', 'architecture-invariant', 'existing-behavior', 'security', 'performance', 'external-standard', 'human'}


def die(message: str) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(2)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        die(f'{path}: {exc}')
    if not isinstance(value, dict):
        die(f'{path} must contain a JSON object')
    return value


def requirement(feature_dir: pathlib.Path) -> str:
    config = feature_dir / 'design.json'
    if not config.exists():
        return 'optional'
    value = load_json(config).get('verification_contract', 'optional')
    return value if value in VALID_REQUIREMENT else 'invalid'


def input_hashes(feature_dir: pathlib.Path, *, repo_root: Callable[[], pathlib.Path], repository: pathlib.Path,
                 constitution: str) -> dict[str, str]:
    root = repo_root() if (feature_dir / 'verification-contract.json').exists() else None
    # The installed harness root is reliable for constitution even if called from another cwd.
    constitution_path = (root or repository) / constitution
    gate = feature_dir / 'design' / 'gate.json'
    master_closure = feature_dir / 'design' / 'master-closure.json'
    result = {
        'spec_sha256': sha(feature_dir / 'spec.md'),
        'plan_sha256': sha(feature_dir / 'plan.md'),
        'constitution_sha256': sha(constitution_path),
    }
    if gate.exists():
        result['design_gate_sha256'] = sha(gate)
    if master_closure.exists():
        result['master_closure_sha256'] = sha(master_closure)
    return result


def validate_contract(feature_dir: pathlib.Path, *, require: bool | None = None,
                      repo_root: Callable[[], pathlib.Path], repository: pathlib.Path, constitution: str,
                      is_active: Callable[[pathlib.Path], bool]) -> list[str]:
    errors: list[str] = []
    required = (requirement(feature_dir) == 'required' and is_active(feature_dir)) if require is None else require
    if requirement(feature_dir) == 'invalid':
        errors.append('design.json.verification_contract must be required/optional/off')
    path = feature_dir / 'verification-contract.json'
    if not path.exists():
        return ['missing required verification-contract.json'] if required else errors
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return errors + [f'invalid JSON in verification-contract.json: {exc}']
    if not isinstance(doc, dict):
        return errors + ['verification-contract.json must contain an object']
    if doc.get('schema_version') != 1:
        errors.append('verification-contract.json schema_version must be 1')
    if doc.get('feature') != feature_dir.name:
        errors.append('verification-contract.json feature must match feature directory')
    if doc.get('status') != 'accepted':
        errors.append('verification-contract.json status must be accepted')
    try:
        expected = input_hashes(feature_dir, repo_root=repo_root, repository=repository, constitution=constitution)
    except (OSError, SystemExit):
        expected = {}
    for key, digest in expected.items():
        if doc.get('inputs', {}).get(key) != digest:
            errors.append(f'verification contract is stale: inputs.{key} does not match current artifact')
    criteria = doc.get('criteria')
    if not isinstance(criteria, list) or not criteria:
        errors.append('verification-contract.json criteria must be non-empty')
        criteria = []
    ids: list[str] = []
    independent = 0
    for i, item in enumerate(criteria):
        where = f'criteria[{i}]'
        if not isinstance(item, dict):
            errors.append(f'{where} must be an object'); continue
        cid = item.get('id')
        if not isinstance(cid, str) or not cid.startswith('VC-'):
            errors.append(f'{where}.id must start with VC-')
        else:
            ids.append(cid)
        if not isinstance(item.get('statement'), str) or not item.get('statement', '').strip():
            errors.append(f'{where}.statement must be non-empty')
        if item.get('origin') not in VALID_ORIGINS:
            errors.append(f'{where}.origin must be spec-derived/independent')
        if item.get('origin') == 'independent':
            independent += 1
        if item.get('source_type') not in VALID_SOURCE_TYPES:
            errors.append(f'{where}.source_type is invalid')
        sources = item.get('sources')
        if not isinstance(sources, list) or not sources or any(not isinstance(x, str) or not x.strip() for x in sources):
            errors.append(f'{where}.sources must be a non-empty string array')
        if not isinstance(item.get('verification_hint'), str) or not item.get('verification_hint', '').strip():
            errors.append(f'{where}.verification_hint must be non-empty')
    if len(ids) != len(set(ids)):
        errors.append('verification criterion ids must be unique')
    if required and independent < 1:
        errors.append('required verification contract must contain at least one independent criterion')
    exemptions = doc.get('exemptions', [])
    if not isinstance(exemptions, list):
        errors.append('verification-contract.json exemptions must be an array')
    else:
        xids: set[str] = set()
        for i, item in enumerate(exemptions):
            where = f'exemptions[{i}]'
            if not isinstance(item, dict):
                errors.append(f'{where} must be an object'); continue
            xid = item.get('id')
            if not isinstance(xid, str) or not xid.startswith('VX-') or xid in xids:
                errors.append(f'{where}.id must be a unique VX-* id')
            else:
                xids.add(xid)
            status = item.get('status')
            if status == 'proposed':
                errors.append(f'{where} is still proposed; a human must explicitly accept/reject it')
            if status not in {'accepted', 'rejected', 'proposed'}:
                errors.append(f'{where}.status is invalid')
            if status == 'accepted':
                approver = item.get('approved_by')
                if not isinstance(approver, str) or not (approver == 'human' or approver.startswith('human:')):
                    errors.append(f'{where} accepted exemption requires approved_by=human or human:<name>')
    return errors


def criterion_ids(feature_dir: pathlib.Path) -> set[str]:
    path = feature_dir / 'verification-contract.json'
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return set()
    return {str(x.get('id')) for x in doc.get('criteria', []) if isinstance(x, dict) and isinstance(x.get('id'), str)}
