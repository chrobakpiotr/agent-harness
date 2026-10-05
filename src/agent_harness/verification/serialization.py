"""Fail-closed structured output boundary; never serialize arbitrary dictionaries.

Known secrets may be supplied by the control process. Environment variables whose
names identify credentials are also collected without retaining their names/values
in any output. Raw logs and command definitions have no structured output field.
"""
import dataclasses
import hashlib
import fnmatch
import json
import math
import os
import re

from agent_harness.trust import SECRET, classify_path
from .model import Evidence

DENY = re.compile(r'(?i)(-----BEGIN .*PRIVATE KEY|(?:password|passwd|secret|token|api[_-]?key|credential)\s*[:=]\s*\S+|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})')
SECRET_NAME = re.compile(r'(?i)(secret|password|passwd|token|credential|api.?key|private.?key)')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')
HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


@dataclasses.dataclass(frozen=True)
class SafetyPolicy:
    known_secrets: tuple[str, ...] = ()

    def safe(self, value):
        return (isinstance(value, str) and
                not DENY.search(value) and
                not any(s in value for s in self.known_secrets if len(s) >= 4))

    def display(self, value):
        return value if self.safe(value) else '<redacted>'


def default_safety():
    return SafetyPolicy(tuple(v for k, v in os.environ.items() if SECRET_NAME.search(k) and len(v) >= 4))


def canonical(value):
    """Legacy versioned record serialization; retained for stored receipts."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')


def canonical_jcs(value):
    """RFC 8785 serialization for Ed25519-signed human authorization data."""
    import math
    def encode(item):
        if item is None:
            return 'null'
        if item is True:
            return 'true'
        if item is False:
            return 'false'
        if isinstance(item, int):
            return str(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError('canonical-json-nonfinite-number')
            # JCS uses ECMAScript shortest-roundtrip binary64 formatting.
            number = repr(item).lower()
            if number.endswith('.0'):
                return number[:-2]
            if 'e' in number:
                mantissa, exponent = number.split('e', 1)
                exponent_value = int(exponent)
                if -6 <= exponent_value < 21:
                    from decimal import Decimal
                    return format(Decimal(number), 'f')
                return f'{mantissa}e{"+" if exponent_value >= 0 else ""}{exponent_value}'
            return number
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(',', ':'))
        if isinstance(item, (list, tuple)):
            return '[' + ','.join(encode(value) for value in item) + ']'
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('canonical-json-object-key-not-string')
            # RFC 8785 orders names by UTF-16 code units.
            keys = sorted(item, key=lambda key: key.encode('utf-16-be', errors='surrogatepass'))
            return '{' + ','.join(encode(key) + ':' + encode(item[key]) for key in keys) + '}'
        raise ValueError('canonical-json-type-unsupported')

    return encode(value).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_path(path, *, pattern=False, allow_dot=False):
    if not isinstance(path, str) or not path or len(path) > 4096:
        return False
    if allow_dot and path == '.':
        return True
    if path.startswith('/') or '\\' in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return False
    if any(p in ('', '.', '..') for p in path.split('/')):
        return False
    if ':' in path or any(c in path for c in '[]{}'):
        return False
    return pattern or not any(c in path for c in '*?')


def diagnostic_provider_artifact_path(path):
    """Known harness runtime outputs that must never become reusable identity.

    Provider/Wayfinder stdout/stderr, runner verify-NN streams and v2 gate
    attempt logs and raw provider envelopes all live here. Product artifacts
    elsewhere may use .log or .json.
    This is an authority exclusion, separate from the secret classifier.
    """
    if not isinstance(path, str) or not path.startswith('.agent-runs/'):
        return False
    name = path.rsplit('/', 1)[-1]
    return (name.endswith('.log') or name in {'provider-envelope.json', 'provider-output.json'} or
            any(fnmatch.fnmatchcase(log, name) for log in
                ('stdout.log', 'stderr.log', 'verify-00.stdout.log',
                 'verify-00.stderr.log', 'attempt.log')))


# Kept as an alias for callers that only need to describe the existing log
# policy; all authority boundaries use the broader semantic classifier.
diagnostic_log_path = diagnostic_provider_artifact_path


def sensitive_path(path):
    """Apply the canonical classifier to both root and nested spellings.

    Its fnmatch **/ rules require a slash. A neutral virtual parent lets those
    same rules recognize root files without copying or changing secret policy.
    This spelling is only classified, never opened or serialized.
    """
    return classify_path(path) == SECRET or classify_path('_verification_root/' + path) == SECRET


ID_FIELDS = {'gate_id', 'profile_gate_id', 'family_id', 'evidence_id', 'ownership_token', 'producer', 'id', 'failure_id'}
HASH_FIELDS = {'fingerprint', 'pre_fingerprint', 'post_fingerprint', 'profile_hash', 'policy_checkpoint',
               'command_hash', 'repository_id', 'receipt_hash', 'content_hash', 'base_sha',
               'candidate_identity', 'final_changed_surface_id'}
ENUMS = {
    'status': {'pass', 'verification-failed', 'environment-blocked', 'invalid-policy', 'invalid-cache',
               'retry-policy-violation', 'stale-input', 'busy', 'harness-error', 'abandoned', 'running'},
    'origin_policy': {'task-completion', 'integration'},
    'decision': {'RUN_NOW', 'ALREADY_GREEN', 'INVALIDATED_BY_THIS_PATCH'},
    'action': {'RUN', 'REUSE'}, 'sandbox': {'required', 'best-effort', 'off'},
    'retry_policy': {'forbid', 'allow'}, 'kind': {'file', 'absent', 'symlink', 'directory'},
    'reason': {'fresh-task-completion', 'no-reusable-evidence', 'exact-evidence', 'inputs-changed',
               'dependency-not-reusable', 'artifact-mismatch', 'non-cacheable-policy',
               'non-cacheable-symlink-input', 'non-cacheable-sensitive-identity',
               'non-cacheable-external-state', 'non-cacheable-probe', 'invalid-cache',
               'different-family', 'legacy-task-command'},
}
NUMBERS = {'duration_seconds', 'started_at', 'ended_at', 'process_invocations', 'exit_code', 'schema_version',
           'input_tokens', 'output_tokens', 'total_tokens', 'cost'}
BOOLS = {'advisory', 'cacheable', 'executable', 'complete'}


def safe_record(record, *, safety=None):
    """Validate keys, types and nested shapes before either persistence or display."""
    safety = safety or default_safety()
    if not isinstance(record, dict):
        raise ValueError('unsafe-structured-record')
    result = {}
    for key, value in record.items():
        valid = False
        if key in ID_FIELDS:
            valid = (key == 'profile_gate_id' and value is None) or (isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)))
        elif key in HASH_FIELDS:
            valid = (key in {'fingerprint', 'content_hash', 'candidate_identity',
                             'final_changed_surface_id'} and value is None) or (isinstance(value, str) and bool(HASH.fullmatch(value)))
        elif key in ENUMS:
            valid = isinstance(value, str) and value in ENUMS[key]
        elif key in NUMBERS:
            valid = type(value) in (int, float) and math.isfinite(value)
        elif key in BOOLS:
            valid = type(value) is bool
        elif key == 'path':
            valid = safe_path(value) and not sensitive_path(value) and not diagnostic_provider_artifact_path(value)
        elif key in ('decisions', 'artifacts', 'dependencies'):
            shapes = {'artifacts': {'path', 'kind', 'content_hash', 'executable'},
                      'dependencies': {'gate_id', 'evidence_id', 'fingerprint'}}
            if not isinstance(value, (tuple, list)):
                raise ValueError('unsafe-structured-record')
            if key in shapes and any(not isinstance(v, dict) or set(v) != shapes[key] for v in value):
                raise ValueError('unsafe-structured-record')
            result[key] = [safe_record(v, safety=safety) for v in value]
            continue
        elif key == 'usage':
            if not isinstance(value, dict) or not set(value) <= {'input_tokens', 'output_tokens', 'total_tokens', 'cost', 'complete'}:
                raise ValueError('unsafe-structured-record')
            result[key] = safe_record(value, safety=safety)
            continue
        if not valid or (isinstance(value, str) and not safety.safe(value)):
            raise ValueError('unsafe-structured-record')
        result[key] = value
    return result


def validate_evidence_record(record, *, safety=None, include_receipt=True):
    """Receipt-specific structural boundary, matching the v2 PASS schema.

    Advisory fields may be nullable; required receipt identities never are.
    Digest/provenance checks remain separate from this structural validation.
    """
    required = set(Evidence.__dataclass_fields__)
    if not include_receipt: required.remove('receipt_hash')
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError('invalid-terminal-evidence')
    result = safe_record(record, safety=safety)
    for key in required & (ID_FIELDS | HASH_FIELDS):
        value = result[key]
        if key in {'candidate_identity', 'final_changed_surface_id'} and value is None:
            continue
        if not isinstance(value, str): raise ValueError('invalid-terminal-evidence')
        if key in HASH_FIELDS and key != 'policy_checkpoint' and len(value) != 64:
            raise ValueError('invalid-terminal-evidence')
    if (type(result['schema_version']) is not int or result['schema_version'] != 2 or
            result['status'] != 'pass' or type(result['exit_code']) is not int or result['exit_code'] != 0 or
            type(result['process_invocations']) is not int or result['process_invocations'] < 1 or
            result['started_at'] < 0 or result['ended_at'] < 0):
        raise ValueError('invalid-terminal-evidence')
    for key in ('artifacts', 'dependencies'):
        seen = set()
        for item in result[key]:
            encoded = canonical(item)
            if encoded in seen: raise ValueError('invalid-terminal-evidence')
            seen.add(encoded)
            hash_key = 'content_hash' if key == 'artifacts' else 'fingerprint'
            if not isinstance(item[hash_key], str) or len(item[hash_key]) != 64:
                raise ValueError('invalid-terminal-evidence')
            if key == 'artifacts' and item['kind'] != 'file':
                raise ValueError('invalid-terminal-evidence')
    return result


def evidence_record(evidence, *, safety=None, include_receipt=True):
    if not isinstance(evidence, Evidence): raise ValueError('invalid-terminal-evidence')
    try:
        record = dataclasses.asdict(evidence)
        record['dependencies'] = [dict(gate_id=g, evidence_id=e, fingerprint=f) for g, e, f in evidence.dependencies]
        if not include_receipt: record.pop('receipt_hash')
        return validate_evidence_record(record, safety=safety, include_receipt=include_receipt)
    except (TypeError, AttributeError, KeyError):
        raise ValueError('invalid-terminal-evidence') from None
