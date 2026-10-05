#!/usr/bin/env python3
"""Reproducible G+M+B planning benchmark: 100 gates, 5,000 x 256-byte files."""
import argparse
import json
import os
import pathlib
import statistics
import subprocess
import tempfile
import time
from unittest import mock

from agent_harness.verification.model import Family
from agent_harness.verification.planner import build_plan
from agent_harness.verification.profile import load_profile


def _run(command, *, cwd=None, env=None):
    subprocess.run(command, cwd=cwd, env=env, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def run_benchmark():
    with tempfile.TemporaryDirectory(prefix='verification-plan-benchmark-') as temporary:
        root = pathlib.Path(temporary)
        _run(['git', 'init', '-q', str(root)])
        (root / 'baseline.txt').write_text('baseline\n', encoding='utf-8')
        env = dict(os.environ, GIT_AUTHOR_NAME='Benchmark', GIT_AUTHOR_EMAIL='bench@example.invalid',
                   GIT_COMMITTER_NAME='Benchmark', GIT_COMMITTER_EMAIL='bench@example.invalid')
        _run(['git', '-C', str(root), 'add', 'baseline.txt'], env=env)
        _run(['git', '-C', str(root), 'commit', '-qm', 'baseline'], env=env)
        base = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()

        gates = []
        for gate_number in range(100):
            gate_id = f'gate-{gate_number:03d}'
            directory = f'bench/{gate_id}'
            gates.append({
                'id': gate_id,
                'command': f'python3 -m unittest synthetic.{gate_id}',
                'inputs': [f'{directory}/**'],
                'applicability': [f'{directory}/**'],
                'mandatory': True,
                'cacheable': False,
            })
            target = root / directory
            target.mkdir(parents=True)
            for file_number in range(50):
                content = f'{gate_number:03d}:{file_number:03d}:'.encode() + b'x' * 248
                if len(content) != 256:
                    raise AssertionError('benchmark file size is not 256 bytes')
                (target / f'input-{file_number:03d}.dat').write_bytes(content)

        profile = load_profile({'schema_version': 1, 'gates': gates})
        family = Family('benchmark-family', base, 'integration', profile.content_hash, 'c' * 64)
        subprocess_calls = []
        original_run = subprocess.run

        def counted_run(command, *args, **kwargs):
            subprocess_calls.append(command[0] if command else '')
            return original_run(command, *args, **kwargs)

        durations = []
        result_counts = []
        with mock.patch('agent_harness.verification.fingerprint.subprocess.run', side_effect=counted_run):
            # Required one warm-up, then three full in-process planner runs.
            warmup = build_plan(root, profile, family)
            if len(warmup.decisions) != 100:
                raise AssertionError('benchmark planner did not derive all 100 gates')
            for _ in range(3):
                start = time.perf_counter()
                plan = build_plan(root, profile, family)
                durations.append(time.perf_counter() - start)
                result_counts.append(len(plan.decisions))

        calls_per_run = len(subprocess_calls) / 4
        if any(call != 'git' for call in subprocess_calls) or calls_per_run > 12:
            raise AssertionError(f'planner launched per-file/non-Git subprocesses: {len(subprocess_calls)}')
        if result_counts != [100, 100, 100]:
            raise AssertionError('benchmark planner gate count changed')
        return {
            'schema_version': 1,
            'fixture': {'gates': 100, 'files': 5000, 'bytes_per_file': 256,
                        'disjoint_matches_per_gate': 50, 'bytes_total': 1280000},
            'runs': 3,
            'warmups': 1,
            'durations_seconds': [round(value, 6) for value in durations],
            'median_seconds': round(statistics.median(durations), 6),
            'maximum_seconds': round(max(durations), 6),
            'git_subprocess_calls_per_run': calls_per_run,
            'operation_contract': 'G+M+B; no subprocess-per-file planning',
            'limits_seconds': {'median': 10, 'maximum': 20},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=pathlib.Path, help='write the JSON result to this path')
    args = parser.parse_args()
    result = run_benchmark()
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(rendered, encoding='utf-8')
    print(rendered, end='')
    if result['median_seconds'] > 10 or result['maximum_seconds'] > 20:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
