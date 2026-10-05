"""Version-one policy loader and platform-independent segment mini-glob."""
import hashlib
import json
import pathlib
import re
from functools import lru_cache

from .model import Artifact, Gate, InvalidPolicy, Probe, Profile
from .serialization import IDENTIFIER, canonical, default_safety, diagnostic_provider_artifact_path, safe_path


@lru_cache(maxsize=4096)
def pattern_segments(pattern):
    if not safe_path(pattern, pattern=True):
        raise InvalidPolicy('invalid-pattern')
    segments = tuple(pattern.split('/'))
    if any('**' in s and s != '**' for s in segments):
        raise InvalidPolicy('invalid-pattern')
    return segments


def segment_matches(pattern, text):
    """Whole-segment DP: O(len(pattern) * len(text)) time, O(len(text)) space.

    Each cell is visited once; a star never recursively retries a suffix.
    The outer NFA owns ** and path separators.
    """
    previous = [True] + [False] * len(text)
    for token in pattern:
        current = [token == '*' and previous[0]] + [False] * len(text)
        for index, char in enumerate(text, 1):
            if token == '*':
                current[index] = previous[index] or current[index - 1]
            else:
                current[index] = previous[index - 1] and (token == '?' or token == char)
        previous = current
    return previous[-1]


def reachable(pattern, path):
    """NFA states after consuming path; ** has a zero-segment epsilon edge."""
    segments = pattern_segments(pattern)
    states = {0}
    def closure(states):
        result = set(states)
        for i in range(len(segments)):
            if i in result and segments[i] == '**': result.add(i + 1)
        return result
    states = closure(states)
    for part in path.split('/'):
        following = set()
        for i in states:
            if i == len(segments): continue
            if segments[i] == '**': following.add(i)
            elif segment_matches(segments[i], part): following.add(i + 1)
        states = closure(following)
    return states


def matches(pattern, path):
    pattern_segments(pattern)
    # Git names may contain punctuation unsupported in declared policy patterns.
    if not isinstance(path, str) or not path or any(p in ('', '.', '..') for p in path.split('/')):
        return False
    return len(pattern_segments(pattern)) in reachable(pattern, path)


def affects_descendants(pattern, path):
    return bool(reachable(pattern, path))


def command_identity(command, cwd='.', *, safety=None):
    safety = safety or default_safety()
    if not isinstance(command, str) or not command.strip() or not safety.safe(command):
        raise InvalidPolicy('unsafe-command-definition')
    if not safe_path(cwd, allow_dot=True) or not safety.safe(cwd):
        raise InvalidPolicy('unsafe-working-directory')
    return hashlib.sha256(canonical([command, cwd])).hexdigest()


def _strings(value, *, patterns=False, paths=False):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value) or len(set(value)) != len(value):
        raise InvalidPolicy('invalid-list')
    for x in value:
        if patterns: pattern_segments(x)
        elif paths and not safe_path(x): raise InvalidPolicy('invalid-path')
        elif not paths and not IDENTIFIER.fullmatch(x): raise InvalidPolicy('invalid-identifier')
    return tuple(value)


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise InvalidPolicy('duplicate-policy-key')
        out[key] = value
    return out


def load_profile(source, *, safety=None):
    """Load only the trusted control-plane snapshot; never discover worktree policy."""
    safety = safety or default_safety()
    try:
        if isinstance(source, dict):
            raw = canonical(source)
            document = json.loads(raw, object_pairs_hook=_no_duplicates)
        else:
            raw = pathlib.Path(source).read_bytes()
            document = json.loads(raw, object_pairs_hook=_no_duplicates)
        if not safety.safe(raw.decode('utf-8')):
            raise InvalidPolicy('unsafe-policy')
        if not isinstance(document, dict) or set(document) != {'schema_version', 'gates'} or type(document['schema_version']) is not int or document['schema_version'] != 1:
            raise InvalidPolicy('unsupported-profile')
        if not isinstance(document['gates'], list): raise InvalidPolicy('invalid-gates')
        gates = []
        allowed = set(Gate.__dataclass_fields__) - {'command_hash'}
        for data in document['gates']:
            if not isinstance(data, dict) or set(data) - allowed or not {'id', 'command', 'inputs'} <= set(data):
                raise InvalidPolicy('invalid-gate')
            values = dict(data)
            if not isinstance(values['id'], str) or not IDENTIFIER.fullmatch(values['id']) or values['id'].startswith(('task-command:', 'legacy-task-command:')):
                raise InvalidPolicy('invalid-gate-id')
            for key in ('mandatory', 'cacheable', 'critical', 'opaque_environment', 'opaque_external_state', 'expensive', 'aggregate'):
                if key in values and type(values[key]) is not bool: raise InvalidPolicy('invalid-boolean')
            for key in ('inputs', 'applicability'):
                if key in values: values[key] = _strings(values[key], patterns=True)
            for key in ('depends_on', 'retry_controls', 'independent_execution_classes'):
                if key in values: values[key] = _strings(values[key])
            if 'produces' in values: values['produces'] = _strings(values['produces'], paths=True)
            if any(key in values and not isinstance(values[key], list) for key in ('probes', 'consumes')):
                raise InvalidPolicy('invalid-list')
            probes = []
            for probe in values.get('probes', []):
                if not isinstance(probe, dict) or set(probe) != {'id', 'format'} or not IDENTIFIER.fullmatch(probe['id']) or probe['format'] not in ('version', 'sha256', 'boolean'):
                    raise InvalidPolicy('invalid-probe')
                probes.append(Probe(**probe))
            if len({p.id for p in probes}) != len(probes): raise InvalidPolicy('duplicate-probe')
            values['probes'] = tuple(probes)
            consumes = []
            for item in values.get('consumes', []):
                if not isinstance(item, dict) or set(item) != {'producer', 'path'} or not safe_path(item['path']) or not IDENTIFIER.fullmatch(item['producer']):
                    raise InvalidPolicy('invalid-artifact')
                consumes.append(Artifact(**item))
            if len(set(consumes)) != len(consumes): raise InvalidPolicy('duplicate-artifact')
            values['consumes'] = tuple(consumes)
            values['command_hash'] = command_identity(values['command'], values.get('cwd', '.'), safety=safety)
            g = Gate(**values)
            if g.sandbox not in ('required', 'best-effort', 'off') or g.retry_policy not in ('forbid', 'allow'):
                raise InvalidPolicy('invalid-execution-policy')
            if any(c != 'harness-managed-independent-execution-v1' for c in g.independent_execution_classes):
                raise InvalidPolicy('invalid-independent-execution-class')
            if g.independent_execution_classes and g.sandbox != 'required':
                raise InvalidPolicy('invalid-independent-sandbox')
            if g.cacheable and any(diagnostic_provider_artifact_path(p) for p in (*g.inputs, *g.produces, *(a.path for a in g.consumes))):
                raise InvalidPolicy('diagnostic-provider-artifact-identity')
            if g.critical and g.retry_policy != 'forbid': raise InvalidPolicy('invalid-critical-retry')
            if not isinstance(g.description, str) or not isinstance(g.category, str) or not IDENTIFIER.fullmatch(g.category):
                raise InvalidPolicy('invalid-description')
            gates.append(g)
        by_id = {g.id: g for g in gates}
        if len(by_id) != len(gates) or len({g.command_hash for g in gates}) != len(gates):
            raise InvalidPolicy('ambiguous-profile')
        visiting, visited = set(), set()
        def visit(id):
            if id in visiting or id not in by_id: raise InvalidPolicy('invalid-dependency-dag')
            if id in visited: return
            visiting.add(id)
            g = by_id[id]
            for d in g.depends_on: visit(d)
            for a in g.consumes:
                if a.producer not in by_id or a.path not in by_id[a.producer].produces:
                    raise InvalidPolicy('missing-artifact-producer')
                visit(a.producer)
            visiting.remove(id)
            visited.add(id)
        for g in gates: visit(g.id)
        produced = [path for g in gates for path in g.produces]
        if len(set(produced)) != len(produced): raise InvalidPolicy('conflicting-artifact-producers')
        for path in produced:
            for g in gates:
                for pattern in g.inputs:
                    if affects_descendants(pattern, path) or any(matches(pattern, '/'.join(path.split('/')[:i])) for i in range(1, len(path.split('/')))):
                        raise InvalidPolicy('artifact-source-overlap')
        return Profile(1, hashlib.sha256(raw).hexdigest(), tuple(gates))
    except InvalidPolicy:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise InvalidPolicy('invalid-profile') from None


def probe_value(probe, value, safety):
    if not isinstance(value, str) or len(value) > 128 or not safety.safe(value): return None
    patterns = {'version': r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?',
                'sha256': r'[0-9a-f]{64}', 'boolean': r'(?:true|false)'}
    return value if re.fullmatch(patterns[probe.format], value) else None
