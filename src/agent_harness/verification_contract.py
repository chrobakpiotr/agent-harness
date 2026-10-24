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

F1 (behaviour change, not verbatim): currency that cannot be established fails closed, and ``contract_binding``
returns the errors together with a binding built from the same bytes, which lifecycle transitions record and compare.
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
INPUT_KEYS = ('spec_sha256', 'plan_sha256', 'constitution_sha256', 'design_gate_sha256', 'master_closure_sha256')
REQUIRED_INPUT_KEYS = frozenset(INPUT_KEYS[:3])
BINDING_VERSION = 1
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
    """F1: only a design.json that does not exist means 'optional'; one that cannot be read raises."""
    return _requirement(feature_dir / 'design.json', _read(feature_dir / 'design.json'))


def _requirement(config: pathlib.Path, raw: bytes | None) -> str:
    if raw is None:
        return 'optional'
    try:
        doc = json.loads(raw.decode('utf-8'))
    except ValueError as exc:
        die(f'{config}: {exc}')
    value = doc.get('verification_contract', 'optional') if isinstance(doc, dict) else None
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


def _read(path: pathlib.Path) -> bytes | None:
    """The file's bytes, or None only when it does not exist; any other failure propagates."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _binding(contract: bytes | None, inputs: dict[str, bytes | None]) -> dict[str, Any]:
    def entry(data: bytes | None) -> dict[str, Any]:
        return {'present': False} if data is None else {'present': True, 'sha256': hashlib.sha256(data).hexdigest()}
    if contract is None:
        return {'version': BINDING_VERSION, 'contract': entry(None)}
    return {'version': BINDING_VERSION, 'contract': entry(contract),
            'inputs': {key: entry(inputs.get(key)) for key in INPUT_KEYS}}


def validate_contract(feature_dir: pathlib.Path, *, require: bool | None = None,
                      repo_root: Callable[[], pathlib.Path], repository: pathlib.Path, constitution: str,
                      is_active: Callable[[pathlib.Path], bool]) -> list[str]:
    return contract_binding(feature_dir, require=require, repo_root=repo_root, repository=repository,
                            constitution=constitution, is_active=is_active)[0]


def contract_binding(feature_dir: pathlib.Path, *, require: bool | None = None,
                     repo_root: Callable[[], pathlib.Path], repository: pathlib.Path, constitution: str,
                     is_active: Callable[[pathlib.Path], bool]) -> tuple[list[str], dict[str, Any] | None]:
    """F1: the contract's errors and its binding, both from one read of the contract and its inputs.

    The binding (versioned) records whether the contract exists and, when it does, its digest and the presence and
    digest of each input in ``INPUT_KEYS``; it is None whenever there are errors. A file that cannot be read, or a
    repository root that cannot be found, is an error and never counts as absent.
    """
    errors: list[str] = []
    try:  # one read decides both requiredness and validity
        policy = _requirement(feature_dir / 'design.json', _read(feature_dir / 'design.json'))
    except OSError as exc:
        return [f'verification contract requirement cannot be established: design.json unreadable '
                f'({type(exc).__name__})'], None
    required = (policy == 'required' and is_active(feature_dir)) if require is None else require
    if policy == 'invalid':
        errors.append('design.json.verification_contract must be required/optional/off')
    path = feature_dir / 'verification-contract.json'
    try:
        raw = _read(path)
    except OSError as exc:
        return errors + [f'verification-contract.json unreadable ({type(exc).__name__})'], None
    if raw is None:
        if required:
            return ['missing required verification-contract.json'], None
        return errors, (None if errors else _binding(None, {}))
    try:
        doc = json.loads(raw.decode('utf-8'))
    except ValueError as exc:
        return errors + [f'invalid JSON in verification-contract.json: {exc}'], None
    if not isinstance(doc, dict):
        return errors + ['verification-contract.json must contain an object'], None
    if doc.get('schema_version') != 1:
        errors.append('verification-contract.json schema_version must be 1')
    if doc.get('feature') != feature_dir.name:
        errors.append('verification-contract.json feature must match feature directory')
    if doc.get('status') != 'accepted':
        errors.append('verification-contract.json status must be accepted')
    try:
        root = repo_root()
    except (OSError, SystemExit):
        return errors + ['verification contract currency cannot be established: repository root unavailable'], None
    # The same set as `input_hashes`; the first three are required, the design files only when present.
    sources = {'spec_sha256': feature_dir / 'spec.md', 'plan_sha256': feature_dir / 'plan.md',
               'constitution_sha256': root / constitution,
               'design_gate_sha256': feature_dir / 'design' / 'gate.json',
               'master_closure_sha256': feature_dir / 'design' / 'master-closure.json'}
    inputs: dict[str, bytes | None] = {}
    for key, source in sources.items():
        try:
            inputs[key] = _read(source)
        except OSError as exc:
            errors.append(f'verification contract currency cannot be established: {key} unreadable '
                          f'({type(exc).__name__})')
            continue
        if inputs[key] is None and key in REQUIRED_INPUT_KEYS:
            errors.append(f'verification contract currency cannot be established: {key} unreadable '
                          f'(FileNotFoundError)')
    recorded = doc.get('inputs', {})
    recorded = recorded if isinstance(recorded, dict) else {}
    for key, data in inputs.items():  # as the source: an absent design input is not compared; the binding records it
        if data is not None and recorded.get(key) != hashlib.sha256(data).hexdigest():
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
    return errors, (None if errors else _binding(raw, inputs))


def criterion_ids(feature_dir: pathlib.Path) -> set[str]:
    path = feature_dir / 'verification-contract.json'
    if not path.exists():
        return set()
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return set()
    return {str(x.get('id')) for x in doc.get('criteria', []) if isinstance(x, dict) and isinstance(x.get('id'), str)}
