"""Create exact-job qualification reports; report validation is delegated to Harness v0.5."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from typing import Any

from agent_harness import __version__ as HARNESS_VERSION, contract

from .b_probes import B1_B10, DockerGradingProbeTarget, run_b1_b10
from .q_lifecycle import DockerLifecycleProbeTarget, Q11_Q16, run_q11_q16
from .q_probes import DockerProbeTarget, Q01_Q10, run_q01_q10


CHECK_IDS = tuple(Q01_Q10) + tuple(Q11_Q16) + tuple(B1_B10)
DEFAULT_WORKLOAD_IMAGE = (
    'python@sha256:9d72651cf7018c1f6a1dd6fd02bd68286631c33620bc0f37b0675b21aab915d5'
)
POLICY_FILES = ('docs/specs/AH5-04B-QUAL-001/spec.md',
                'docs/specs/AH5-04B-QUAL-001/verification-contract.json')
_SAFE_ID = re.compile(r'[^A-Za-z0-9._:/@+-]+')
PINNED_HARNESS_VERSION = '0.5.0'


def policy_digest(spec_path: pathlib.Path, verification_contract_path: pathlib.Path) -> str:
    """Bind the report to the accepted behavior and its independent verification contract."""
    manifest = {}
    for path in sorted((spec_path, verification_contract_path), key=lambda item: item.name):
        manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def evidence_reference(path: pathlib.Path, evidence_root: pathlib.Path) -> dict[str, Any]:
    root = evidence_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError('check evidence must be a regular file below evidence_root')
    payload = resolved.read_bytes()
    return {'path': resolved.relative_to(root).as_posix(),
            'sha256': 'sha256:' + hashlib.sha256(payload).hexdigest(), 'size': len(payload)}


def assemble_report(*, target: str, policy_digest: str, author: str,
                    target_tuple: dict[str, str], checks: list[dict[str, Any]],
                    evidence_root: pathlib.Path, independent_review: dict[str, Any] | None = None,
                    created_at: str | None = None) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    evidence_paths: set[str] = set()
    for record in checks:
        check_id = record.get('check_id')
        if check_id not in CHECK_IDS or check_id in by_id:
            raise ValueError(f'invalid or duplicate qualification check: {check_id!r}')
        status = record.get('status')
        if status not in {'pass', 'fail', 'not-run'}:
            raise ValueError(f'invalid qualification status for {check_id}')
        reference = evidence_reference(pathlib.Path(record['evidence_path']), evidence_root)
        if reference['path'] in evidence_paths:
            raise ValueError('every qualification check must have distinct evidence')
        evidence_paths.add(reference['path'])
        refs = [reference]
        by_id[check_id] = {'id': check_id, 'result': status, 'evidence': refs}
    if set(by_id) != set(CHECK_IDS):
        raise ValueError('qualification report must contain all Q01-Q16 and B1-B10 checks exactly once')
    report = {
        'contract_version': 1, 'target': target, 'policy_digest': policy_digest, 'author': author,
        'tuple': target_tuple, 'checks': [by_id[check_id] for check_id in CHECK_IDS],
        'independent_review': independent_review, 'created_at': created_at or _utc_now(),
    }
    return contract.validate_qualification_report(report)


def capability_report(report: dict[str, Any], evidence_root: pathlib.Path) -> dict[str, Any]:
    """Qualification is evidence only; this feature never enables a workload backend."""
    contract.validate_qualification_report(report)
    all_checks_pass = all(check['result'] == 'pass' for check in report['checks'])
    supported = all_checks_pass
    try:
        qualified = all_checks_pass and contract.qualification_passes(report, evidence_root)
    except (OSError, ValueError):
        qualified = False
    return contract.validate_capability_report({
        'contract_version': 1, 'target': report['target'], 'policy_digest': report['policy_digest'],
        'discovered': True, 'supported': supported, 'qualified': qualified, 'launch_ready': False,
        'capabilities': ['qualified_isolation'] if qualified else [],
        'refusal': 'BACKEND_UNAVAILABLE' if qualified else 'NOT_QUALIFIED',
    })


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _safe(value: str) -> str:
    text = _SAFE_ID.sub('_', value.strip()).strip('_')
    if not text:
        raise ValueError('target identity is empty')
    return text[:128]


def _docker_text(docker: str, *args: str) -> str:
    proc = subprocess.run([docker, *args], capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f'{pathlib.Path(docker).name} identity probe failed with exit {proc.returncode}')
    return proc.stdout.strip()


def _target_tuple(target_kind: str, docker: str, workload_image: str, job_id: str,
                  identity_errors: list[str] | None = None) -> dict[str, str]:
    errors = identity_errors if identity_errors is not None else []
    try:
        info = _docker_text(docker, 'info', '--format={{.OSType}}|{{.OperatingSystem}}|{{.KernelVersion}}')
        fields = info.split('|', 2)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        fields = []
        errors.append('docker_info_unavailable')
    if len(fields) != 3 or fields[0] != 'linux' or not fields[1] or not fields[2]:
        errors.append('docker_target_identity_incomplete')
    try:
        engine = _docker_text(docker, 'version', '--format={{.Server.Version}}')
    except (OSError, RuntimeError, subprocess.SubprocessError):
        engine = ''
        errors.append('docker_engine_unavailable')
    digest = workload_image.rsplit('@sha256:', 1)[1]
    try:
        image_digests = json.loads(_docker_text(
            docker, 'image', 'inspect', '--format={{json .RepoDigests}}', workload_image,
        ))
        if not isinstance(image_digests, list) or not any(
                isinstance(item, str) and item.endswith('@sha256:' + digest) for item in image_digests):
            errors.append('workload_image_digest_not_observed')
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError):
        errors.append('workload_image_digest_unavailable')
    if target_kind == 'github-runner':
        runner_os = os.environ.get('RUNNER_OS')
        image_os, image_version = os.environ.get('ImageOS'), os.environ.get('ImageVersion')
        if runner_os != 'Linux' or not image_os or not image_version:
            errors.append('github_runner_image_identity_unavailable')
        host = _safe(f'GitHubHosted_{image_os}_{image_version}') if image_os and image_version else 'unavailable'
    else:
        host = _safe(fields[1]) if len(fields) == 3 and fields[1] else 'unavailable'
        if len(fields) == 3 and 'docker desktop' not in fields[1].casefold():
            errors.append('docker_desktop_identity_mismatch')
    kernel = _safe(fields[2]) if len(fields) == 3 and fields[2] else 'unavailable'
    return {
        'job_id': _safe(job_id), 'host': host, 'kernel': kernel,
        'engine': _safe(f'DockerEngine_{engine}') if engine else 'unavailable',
        'workload_image': 'sha256:' + workload_image.rsplit('@sha256:', 1)[1],
    }


def _current_job_id() -> str:
    run_id = os.environ.get('GITHUB_RUN_ID')
    if run_id:
        attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
        job = os.environ.get('GITHUB_JOB', 'job')
        return _safe(f'{run_id}-attempt-{attempt}-{job}')
    return _safe(f'local-{dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")}-{uuid.uuid4().hex[:12]}')


def _persist_check_job_id(records: list[Any], target_id: str, job_id: str) -> list[dict[str, Any]]:
    result = []
    for record in records:
        path = pathlib.Path(record.evidence_path)
        observed = json.loads(path.read_text(encoding='utf-8'))
        observed['job_id'] = job_id
        observed['target_id'] = target_id
        path.write_text(json.dumps(observed, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        result.append({'check_id': record.check_id, 'status': record.status, 'evidence_path': path})
    return result


def _build_probe_targets(image: str, target_id: str, docker: str, timeout_seconds: int):
    q_target = DockerProbeTarget(image, docker=docker, timeout_seconds=timeout_seconds)
    lifecycle = DockerLifecycleProbeTarget(
        image, docker=docker, timeout_seconds=timeout_seconds,
    )
    grading = DockerGradingProbeTarget(
        image, docker=docker, timeout_seconds=timeout_seconds, target_id=target_id,
    )
    return q_target, lifecycle, grading


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


def _measurement_exit_code(checker_exit_code: int) -> int:
    """A valid non-passing target report is completed evidence, not a runner failure."""
    if checker_exit_code in {0, 1}:
        return 0
    return checker_exit_code if checker_exit_code > 0 else 2


def _checker_command(*arguments: str) -> list[str]:
    if HARNESS_VERSION != PINNED_HARNESS_VERSION:
        raise RuntimeError(
            f'qualification requires agent-harness {PINNED_HARNESS_VERSION}; found {HARNESS_VERSION}'
        )
    return [sys.executable, '-m', 'agent_harness', *arguments]


def run(args: argparse.Namespace) -> int:
    image = args.workload_image
    if '@sha256:' not in image or not re.fullmatch(r'[0-9a-f]{64}', image.rsplit('@sha256:', 1)[1]):
        raise ValueError('workload image must be pinned by an exact sha256 digest')
    target_id = args.target_id or {
        'docker-desktop': 'showcase-docker-desktop-linux-guest',
        'github-runner': 'showcase-github-hosted-ubuntu-runner',
    }[args.target]
    job_id = _safe(args.job_id or _current_job_id())
    evidence_root = args.evidence_root.resolve()
    if evidence_root.exists():
        if evidence_root.is_symlink() or not evidence_root.is_dir() or any(evidence_root.iterdir()):
            raise ValueError('evidence root must be a new, empty directory')
    evidence_root.mkdir(parents=True, exist_ok=True)
    target_identity_errors: list[str] = []
    target_tuple = _target_tuple(args.target, args.docker, image, job_id, target_identity_errors)

    records = []
    if target_identity_errors:
        identity_details = {'target_identity_errors': sorted(set(target_identity_errors))}

        def unavailable(check_id: str, name: str, check_dir: pathlib.Path,
                        _job_id: str | None = None) -> dict[str, Any]:
            return {'status': 'not-run', 'reason_code': 'TARGET_IDENTITY_UNAVAILABLE',
                    'stdout': '', 'stderr': '', 'details': identity_details}

        records.extend(run_q01_q10(unavailable, evidence_root))
        records.extend(run_q11_q16(unavailable, evidence_root, job_id=job_id))
        records.extend(run_b1_b10(unavailable, evidence_root))
    else:
        q_target, lifecycle, grading = _build_probe_targets(
            image, target_id, args.docker, args.timeout_seconds,
        )
        records.extend(run_q01_q10(q_target.execute_q01_q10, evidence_root))
        records.extend(run_q11_q16(lifecycle.execute_q11_q16, evidence_root, job_id=job_id))
        records.extend(run_b1_b10(grading.execute_b1_b10, evidence_root))
    report_checks = _persist_check_job_id(records, target_id, job_id)

    repo_root = args.repository_root.resolve(strict=True)
    digest = policy_digest(repo_root / POLICY_FILES[0], repo_root / POLICY_FILES[1])
    report = assemble_report(
        target=target_id, policy_digest=digest, author=args.author,
        target_tuple=target_tuple, checks=report_checks, evidence_root=evidence_root,
    )
    capability = capability_report(report, evidence_root)
    _write_json(args.report, report)
    _write_json(args.capability_report, capability)

    command = _checker_command(
        'qualification', '--check', str(args.report), '--evidence-root', str(evidence_root),
        '--capability-report', str(args.capability_report), '--job-id', job_id,
    )
    checked = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    _write_json(evidence_root / 'checker-result.json', {
        'command': command, 'exit_code': checked.returncode,
        'stdout': checked.stdout[:4000], 'stderr': checked.stderr[:4000],
    })
    print(checked.stdout, end='')
    if checked.stderr:
        print(checked.stderr, end='', file=sys.stderr)
    if checked.returncode == 1:
        print('qualification run completed; target remains NOT QUALIFIED')
    return _measurement_exit_code(checked.returncode)


def attach_review(args: argparse.Namespace) -> int:
    report = json.loads(args.report.read_text(encoding='utf-8'))
    root = args.evidence_root.resolve(strict=True)
    evidence = args.review_evidence.resolve(strict=True)
    if not evidence.is_relative_to(root):
        raise ValueError('review evidence must be under the qualification evidence root')
    subject = contract.review_subject(report)
    body = evidence.read_bytes()
    if subject.encode() not in body:
        raise ValueError('review evidence must name the exact review_subject')
    report['independent_review'] = {
        'reviewer': args.reviewer, 'subject': subject, 'verdict': args.verdict,
        'evidence': evidence_reference(evidence, root),
    }
    contract.validate_qualification_report(report)
    capability = capability_report(report, root)
    _write_json(args.report, report)
    _write_json(args.capability_report, capability)
    command = _checker_command(
        'qualification', '--check', str(args.report), '--evidence-root', str(root),
        '--capability-report', str(args.capability_report), '--job-id', report['tuple']['job_id'],
    )
    checked = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    _write_json(root / 'checker-result.json', {'command': command, 'exit_code': checked.returncode,
                                               'stdout': checked.stdout[:4000], 'stderr': checked.stderr[:4000]})
    print(checked.stdout, end='')
    if checked.stderr:
        print(checked.stderr, end='', file=sys.stderr)
    return checked.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run', help='execute and record all 26 checks on one exact target')
    run_parser.add_argument('--target', choices=('docker-desktop', 'github-runner'), required=True)
    run_parser.add_argument('--target-id')
    run_parser.add_argument('--job-id')
    run_parser.add_argument('--author', default=os.environ.get('GITHUB_ACTOR', 'showcase-qualifier'))
    run_parser.add_argument('--workload-image', default=DEFAULT_WORKLOAD_IMAGE)
    run_parser.add_argument('--docker', default='docker')
    run_parser.add_argument('--timeout-seconds', type=int, default=30)
    run_parser.add_argument('--repository-root', type=pathlib.Path,
                            default=pathlib.Path(__file__).resolve().parents[3])
    run_parser.add_argument('--sandbox-source', type=pathlib.Path,
                            default=pathlib.Path(__file__).resolve().parents[1] / 'verification_sandbox.py')
    run_parser.add_argument('--evidence-root', type=pathlib.Path, required=True)
    run_parser.add_argument('--report', type=pathlib.Path, required=True)
    run_parser.add_argument('--capability-report', type=pathlib.Path, required=True)
    run_parser.set_defaults(handler=run)

    review = sub.add_parser('attach-review', help='bind an independently authored review to the exact report')
    review.add_argument('--report', type=pathlib.Path, required=True)
    review.add_argument('--evidence-root', type=pathlib.Path, required=True)
    review.add_argument('--review-evidence', type=pathlib.Path, required=True)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--verdict', choices=('pass', 'fail'), required=True)
    review.add_argument('--capability-report', type=pathlib.Path, required=True)
    review.set_defaults(handler=attach_review)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'qualification report: {type(exc).__name__}: {str(exc)[:200]}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
