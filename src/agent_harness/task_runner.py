"""Provider-neutral checks for one already-planned SDD task run (AH5-12a).

Packet integrity, the task-result schema projection and validation, git/path postconditions and the
accepted-plan verification hook, verbatim from Showcase `tooling/agent-harness/runner.py` at `49ab97f`. The
consumer's tested schema-projection profiles, canonical schema, verification CLI, profile root and environment
builder are explicit parameters. The provider launch is not here; it moves onto `agent_harness.execution`
(AH5-12b). See docs/migration/provenance.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
from typing import Any

from agent_harness import trust
from agent_harness.executables import (
    ExecutableResolutionError,
    verify_executable_binding,
)
from agent_harness.machine_outcomes import is_control_outcome

SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    '$schema', 'title', 'type', 'additionalProperties', 'required', 'properties',
    'enum', 'minLength', 'items', 'uniqueItems', 'pattern',
})


def die(message: str) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(2)


def load(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        die(str(exc))
    if not isinstance(value, dict):
        die(f'expected JSON object: {path}')
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def project_task_result_schema(
    schema: dict[str, Any], provider: str, provider_cli_version: str, *, profiles,
) -> tuple[dict[str, Any], list[str]]:
    """Generate one tested provider schema without weakening canonical validation."""
    # [seam] `profiles`: the consumer's tested (provider, CLI version) -> projection profiles; none built in.
    profile = profiles.get((provider, provider_cli_version))
    if profile is None:
        raise ValueError(f'unsupported task-result schema profile: {provider} {provider_cli_version!r}')
    definition_errors = schema_definition_errors(schema)
    if definition_errors:
        raise ValueError(f'canonical task-result schema cannot be projected: {definition_errors[:8]}')
    dropped: list[str] = []
    drop_keywords = profile['drop_keywords']

    def project(node: Any, pointer: str) -> Any:
        if isinstance(node, list):
            return [project(child, f'{pointer}/{index}') for index, child in enumerate(node)]
        if not isinstance(node, dict):
            return node
        unknown = set(node) - SUPPORTED_SCHEMA_KEYWORDS
        if unknown:
            raise ValueError(f'unsupported canonical schema keywords at {pointer or "/"}: {sorted(unknown)}')
        out: dict[str, Any] = {}
        for key, child in node.items():
            child_pointer = f'{pointer}/{key}'
            if key in drop_keywords:
                dropped.append(child_pointer)
                continue
            if key == 'properties':
                if not isinstance(child, dict):
                    raise ValueError(f'properties must be an object at {child_pointer}')
                out[key] = {name: project(value, f'{child_pointer}/{name}')
                            for name, value in sorted(child.items())}
            elif key == 'items':
                out[key] = project(child, child_pointer)
            elif key in {'additionalProperties', 'required'} and node.get('type') == 'object':
                # Object openness and optional fields are not portable in strict output modes.
                continue
            else:
                out[key] = project(child, child_pointer)
        if node.get('type') == 'object':
            properties = node.get('properties')
            if not isinstance(properties, dict):
                raise ValueError(f'object schema has no properties object at {pointer or "/"}')
            out['additionalProperties'] = False
            out['required'] = sorted(properties)
        return out

    result = project(schema, '')
    if not isinstance(result, dict):
        raise ValueError('canonical task-result schema must be an object')
    return result, sorted(dropped)


def task_result_projection(
    canonical_schema_bytes: bytes, provider: str, provider_cli_version: str, *, profiles,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    try:
        canonical_schema = json.loads(canonical_schema_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f'invalid canonical task-result schema: {exc}') from exc
    output_schema, dropped_constraints = project_task_result_schema(
        canonical_schema, provider, provider_cli_version, profiles=profiles)
    output_schema_bytes = canonical_json(output_schema)
    metadata = {
        'provider': provider,
        'cli_version': provider_cli_version,
        'canonical_schema_sha256': hashlib.sha256(canonical_schema_bytes).hexdigest(),
        'projection_sha256': hashlib.sha256(output_schema_bytes).hexdigest(),
        'dropped_constraints': dropped_constraints,
    }
    return canonical_schema, output_schema_bytes, metadata


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return (left.keys() == right.keys() and
                all(_json_equal(left[key], right[key]) for key in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def schema_definition_errors(schema: Any) -> list[str]:
    """Reject schema constructs this dependency-free canonical validator cannot enforce."""
    errors: list[str] = []
    known_types = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}

    def visit(rule: Any, pointer: str) -> None:
        if not isinstance(rule, dict):
            errors.append(f'{pointer or "/"}: schema node must be an object')
            return
        unknown = set(rule) - SUPPORTED_SCHEMA_KEYWORDS
        if unknown:
            errors.append(f'{pointer or "/"}: unsupported schema keywords {sorted(unknown)}')
            return
        expected = rule.get('type')
        types = expected if isinstance(expected, list) else ([expected] if expected is not None else [])
        if any(not isinstance(item, str) or item not in known_types for item in types):
            errors.append(f'{pointer or "/"}: unsupported schema type {expected!r}')
        if 'required' in rule and (
            not isinstance(rule['required'], list) or
            any(not isinstance(item, str) for item in rule['required'])
        ):
            errors.append(f'{pointer or "/"}: required must be an array of strings')
        if 'properties' in rule:
            if not isinstance(rule['properties'], dict):
                errors.append(f'{pointer or "/"}: properties must be an object')
            else:
                for name, child in rule['properties'].items():
                    visit(child, f'{pointer}/properties/{name}')
        if 'items' in rule:
            visit(rule['items'], f'{pointer}/items')
        additional = rule.get('additionalProperties', True)
        if not isinstance(additional, (bool, dict)):
            errors.append(f'{pointer or "/"}: additionalProperties must be boolean or schema')
        elif isinstance(additional, dict):
            visit(additional, f'{pointer}/additionalProperties')
        if 'enum' in rule and not isinstance(rule['enum'], list):
            errors.append(f'{pointer or "/"}: enum must be an array')
        if 'minLength' in rule and (
            type(rule['minLength']) is not int or rule['minLength'] < 0
        ):
            errors.append(f'{pointer or "/"}: minLength must be a non-negative integer')
        if 'pattern' in rule:
            if not isinstance(rule['pattern'], str):
                errors.append(f'{pointer or "/"}: pattern must be a string')
            else:
                try:
                    re.compile(rule['pattern'])
                except re.error:
                    errors.append(f'{pointer or "/"}: pattern is invalid')
        if 'uniqueItems' in rule and type(rule['uniqueItems']) is not bool:
            errors.append(f'{pointer or "/"}: uniqueItems must be boolean')

    visit(schema, '')
    return errors


def canonical_schema_errors(value: Any, schema: dict[str, Any]) -> list[str]:
    """Validate the repository's deliberately small JSON Schema subset fail-closed."""
    errors = schema_definition_errors(schema)
    if errors:
        return errors

    def visit(instance: Any, rule: Any, pointer: str) -> None:
        if not isinstance(rule, dict):
            errors.append(f'{pointer or "/"}: invalid schema node')
            return
        expected = rule.get('type')
        expected_types = expected if isinstance(expected, list) else [expected]
        type_names = {
            'object': lambda item: isinstance(item, dict),
            'array': lambda item: isinstance(item, list),
            'string': lambda item: isinstance(item, str),
            'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
            'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            'boolean': lambda item: isinstance(item, bool),
            'null': lambda item: item is None,
        }
        if expected is not None:
            unknown_types = [name for name in expected_types if name not in type_names]
            if unknown_types:
                errors.append(f'{pointer or "/"}: unsupported schema types {unknown_types}')
                return
            if not any(type_names[name](instance) for name in expected_types):
                errors.append(f'{pointer or "/"}: expected {expected}, got {type(instance).__name__}')
                return

        if 'enum' in rule and not any(_json_equal(instance, item) for item in rule['enum']):
            errors.append(f'{pointer or "/"}: value is not in enum')
        if isinstance(instance, str):
            if len(instance) < rule.get('minLength', 0):
                errors.append(f'{pointer or "/"}: string shorter than minLength')
            pattern = rule.get('pattern')
            if pattern is not None:
                try:
                    if re.search(pattern, instance) is None:
                        errors.append(f'{pointer or "/"}: string does not match pattern')
                except re.error:
                    errors.append(f'{pointer or "/"}: invalid schema pattern')

        if isinstance(instance, dict):
            properties = rule.get('properties', {})
            if not isinstance(properties, dict):
                errors.append(f'{pointer or "/"}: invalid properties schema')
                return
            for name in rule.get('required', []):
                if name not in instance:
                    errors.append(f'{pointer}/{name}: required property is missing' if pointer else
                                  f'/{name}: required property is missing')
            additional = rule.get('additionalProperties', True)
            for name, child in instance.items():
                child_pointer = f'{pointer}/{name}'
                if name in properties:
                    visit(child, properties[name], child_pointer)
                elif additional is False:
                    errors.append(f'{child_pointer}: additional property is not allowed')
                elif isinstance(additional, dict):
                    visit(child, additional, child_pointer)
        elif isinstance(instance, list):
            if 'items' in rule:
                for index, child in enumerate(instance):
                    visit(child, rule['items'], f'{pointer}/{index}')
            if rule.get('uniqueItems') is True:
                for index, child in enumerate(instance):
                    if any(_json_equal(child, earlier) for earlier in instance[:index]):
                        errors.append(f'{pointer or "/"}: uniqueItems constraint violated')
                        break

    visit(value, schema, '')
    return errors


def packet_hash(packet: dict[str, Any]) -> str:
    payload = dict(packet)
    payload.pop('packet_sha256', None)
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(canonical).hexdigest()


def validate_packet_integrity(packet: dict[str, Any]) -> None:
    expected = packet.get('packet_sha256')
    if not isinstance(expected, str) or not expected:
        die('task packet is missing packet_sha256')
    actual = packet_hash(packet)
    if actual != expected:
        die('task packet integrity check failed; regenerate it after deliberate re-planning')
    if packet.get('protocol_version') not in {3, 4}:
        die(f'unsupported task packet protocol_version={packet.get("protocol_version")!r}')

    context = packet.get('context')
    if not isinstance(context, dict):
        die('task packet context must be an object')
    for name, raw in context.items():
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            die(f'task packet context.{name} must be a relative repository path')
        path = pathlib.PurePosixPath(raw.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts:
            die(f'task packet context.{name} escapes the task worktree: {raw!r}')

    declared_trust = packet.get('context_trust', {})
    if packet.get('protocol_version') >= 4:
        if not isinstance(declared_trust, dict):
            die('task packet context_trust must be an object')
        expected_trust = trust.packet_context_trust(context)
        if declared_trust != expected_trust:
            die(f'task packet context_trust mismatch; expected={expected_trust}, actual={declared_trust}')

    lease = packet.get('lease_policy')
    if not isinstance(lease, dict) or not isinstance(lease.get('ttl_seconds'), int):
        die('task packet lease_policy is missing or invalid')


def validate_result(
    result: dict[str, Any], packet: dict[str, Any] | None = None,
    *, canonical_schema: dict[str, Any],
) -> None:
    # [seam] The consumer's canonical task-result schema; the source loaded its own file by default.
    schema = canonical_schema
    schema_errors = canonical_schema_errors(result, schema)
    if schema_errors:
        die(f'agent result violates canonical task-result schema: {schema_errors[:8]}')
    required = {'status', 'summary', 'changed_paths', 'commands', 'assumptions', 'residual_risks'}
    missing = required - set(result)
    if missing:
        die(f'agent result missing fields: {sorted(missing)}')
    if result['status'] not in {'pass', 'fail', 'needs-human'}:
        die('agent result has invalid status')
    if not isinstance(result['summary'], str) or not result['summary'].strip():
        die('agent result summary must be a non-empty string')
    for field in ('changed_paths', 'commands', 'assumptions', 'residual_risks', 'findings', 'rework_tasks'):
        if field not in result:
            continue
        if not isinstance(result[field], list):
            die(f'agent result {field} must be an array')
        if any(not isinstance(item, str) for item in result[field]):
            die(f'agent result {field} must contain only strings')
    if len(set(result.get('rework_tasks', []))) != len(result.get('rework_tasks', [])):
        die('agent result rework_tasks must be unique')
    if packet and result.get('status') == 'pass' and packet.get('test_policy') == 'risk-driven' and packet.get('test_mode') == 'red-green-refactor':
        evidence = result.get('tdd_evidence')
        if not isinstance(evidence, dict):
            die('passing red-green-refactor builder result requires tdd_evidence')
        for phase in ('red', 'green', 'refactor'):
            if not isinstance(evidence.get(phase), str) or not evidence.get(phase, '').strip():
                die(f'tdd_evidence.{phase} must be a non-empty string')


def git_changed_paths(worktree: pathlib.Path) -> list[str]:
    tracked = subprocess.run(
        ['git', 'diff', '--name-only', 'HEAD'], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    untracked = subprocess.run(
        ['git', 'ls-files', '--others', '--exclude-standard'], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return sorted(set(p for p in tracked + untracked if p))


def path_allowed(path: str, patterns: list[str]) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _hash_untracked_entry(digest: Any, worktree: pathlib.Path, raw: bytes) -> None:
    # Never dereference an untracked symlink while fingerprinting a task worktree.
    # Besides preventing reads outside the worktree, hashing the link target itself
    # also makes broken symlink retargeting visible to reviewer-mutation checks.
    path = worktree / raw.decode('utf-8', errors='surrogateescape')
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        # A concurrent delete is still a meaningful state. A later snapshot will
        # differ rather than causing us to follow a replacement path.
        digest.update(b'missing\0')
        return

    if stat.S_ISLNK(metadata.st_mode):
        digest.update(b'symlink\0')
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise RuntimeError(f'cannot safely fingerprint untracked symlink {path}: {exc}') from exc
        digest.update(os.fsencode(target))
        digest.update(b'\0')
        return

    if not stat.S_ISREG(metadata.st_mode):
        # Do not open FIFOs/devices/sockets. Their type is sufficient for mutation
        # detection and, critically, avoids blocking or reading outside the worktree.
        digest.update(b'special\0')
        digest.update(str(stat.S_IFMT(metadata.st_mode)).encode('ascii'))
        digest.update(b'\0')
        return

    digest.update(b'file\0')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f'cannot safely open untracked file {path}: {exc}') from exc

    try:
        opened = os.fstat(fd)
        current = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RuntimeError(f'untracked file changed type or identity while fingerprinting: {path}')

        content_digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            content_digest.update(chunk)
        digest.update(content_digest.digest())
    finally:
        os.close(fd)


def worktree_content_fingerprint(worktree: pathlib.Path) -> str:
    digest = hashlib.sha256()
    diff = subprocess.run(
        ['git', 'diff', '--binary', '--no-ext-diff', 'HEAD'], cwd=worktree, capture_output=True, check=True,
    ).stdout
    digest.update(diff)
    untracked = subprocess.run(
        ['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=worktree, capture_output=True, check=True,
    ).stdout.split(b'\0')
    for raw in sorted(p for p in untracked if p):
        digest.update(raw + b'\0')
        _hash_untracked_entry(digest, worktree, raw)
    return digest.hexdigest()


def git_snapshot(worktree: pathlib.Path) -> tuple[str, str, str]:
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=worktree, capture_output=True, text=True, check=True).stdout.strip()
    remotes = subprocess.run(['git', 'remote', '-v'], cwd=worktree, capture_output=True, text=True, check=True).stdout
    return head, remotes, worktree_content_fingerprint(worktree)


def enforce_postconditions(
    packet: dict[str, Any], worktree: pathlib.Path, before: tuple[str, str, str], result: dict[str, Any], *, review_existing: bool = False,
) -> None:
    head_before, remotes_before, content_before = before
    head_after, remotes_after, content_after = git_snapshot(worktree)
    if head_after != head_before:
        die('agent changed HEAD (commit/reset/rebase); task runs must leave commit history untouched')
    if remotes_after != remotes_before:
        die('agent changed git remote configuration')
    if review_existing and content_after != content_before:
        die('reviewer modified the implementation; review-existing runs are read-only')
    actual = git_changed_paths(worktree)
    allowed = [p for p in packet.get('allowed_paths', []) if isinstance(p, str)]
    violations = [p for p in actual if not path_allowed(p, allowed)]
    if violations:
        die(f'agent modified paths outside task packet: {violations}')
    reported = sorted(set(str(p) for p in result.get('changed_paths', [])))
    if reported != actual:
        die(f'agent changed_paths evidence differs from git diff; reported={reported}, actual={actual}')


def run_verification(
    packet: dict[str, Any], worktree: pathlib.Path, out: pathlib.Path, timeout_seconds: int,
    sandbox_mode: str = 'auto', accepted_plan_id: str | None = None, *, resolve_accepted, profile_root,
    verify_cli, safe_env, verify_binding=verify_executable_binding,
) -> tuple[bool, list[dict[str, Any]]]:
    """Consume an exact orchestration-accepted plan; never plan or authorize here."""
    # [seam] The consumer's accepted-plan resolver, trusted profile root, verification CLI script, isolated
    # environment builder and executable-binding check; the source imported Showcase's modules and used its
    # own directory.
    if not accepted_plan_id:
        return False, [{'command': '<accepted-plan>', 'exit_code': 5,
                        'machine_category': 'verification-blocked',
                        'reason_code': 'VERIFICATION_EXECUTION_PLAN_REQUIRED'}]
    try:
        from agent_harness.verification.profile import (
            load_profile,  # looked up per call, as in the source
        )
        record = resolve_accepted(worktree, accepted_plan_id)
        profile_path = pathlib.Path(profile_root) / f"{record['profile_id']}.json"
        profile = load_profile(profile_path, repository=worktree)
        if profile.content_hash != record.get('profile_hash'):
            raise ValueError('PLAN_BINDING_MISMATCH')
        control_python = record.get('control_executable_binding')
        if not isinstance(control_python, dict):
            raise ValueError('PLAN_BINDING_MISMATCH')
        control_env = safe_env(
            worktree, out / 'verification-control-home', profile.toolchain_directories)
        verify_binding(control_python)
        result = subprocess.run([control_python['path'], '-I', str(verify_cli), 'run',
            '--mode', 'integration', '--repo', str(worktree), '--plan-id', accepted_plan_id],
            cwd=worktree, env=control_env, text=True, capture_output=True,
            timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return False, [{'command': '<accepted-plan>', 'exit_code': None,
                        'outcome': 'TIMEOUT', 'plan_id': accepted_plan_id}]
    except (OSError, RuntimeError, ValueError, ExecutableResolutionError) as exc:
        reason = str(exc) if str(exc) in {'PLAN_BINDING_MISMATCH'} else 'ACCEPTED_PLAN_UNAVAILABLE'
        return False, [{'command': '<accepted-plan>', 'exit_code': 5,
                        'machine_category': 'verification-blocked', 'reason_code': reason,
                        'plan_id': accepted_plan_id}]
    try:
        authority_result = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        authority_result = {}
    category = authority_result.get('machine_category', authority_result.get('status'))
    if is_control_outcome(category):
        return False, [{'command': '<accepted-plan>', 'exit_code': result.returncode,
            'machine_category': category, 'reason_code': authority_result.get('reason_code', 'CONTROL_OUTCOME'),
            'plan_id': accepted_plan_id}]
    if result.returncode != 0 or authority_result.get('outcome') != 'PASS':
        return False, [{'command': '<accepted-plan>', 'exit_code': result.returncode,
            'outcome': authority_result.get('outcome', 'ERROR'), 'plan_id': accepted_plan_id}]
    return True, [{'command': '<accepted-plan>', 'exit_code': 0,
                   'outcome': 'PASS', 'plan_id': accepted_plan_id}]
