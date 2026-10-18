"""Thin read-only CLI for the shared advisory verification planner.

From Showcase `tooling/agent-harness/verify.py` at `49ab97f` (AH5-11). The trusted profile root and the
consumer-bound store, authority resolver and executor are required parameters; the source took them from its file
location and its repository-local modules. See docs/migration/provenance.md.
"""
import argparse
import json
import pathlib

from agent_harness.machine_outcomes import exit_code
from agent_harness.verification.model import Family, InvalidPolicy
from agent_harness.verification.planner import build_plan
from agent_harness.verification.profile import load_profile
from agent_harness.verification.store import StoreError


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes untrusted arguments in its diagnostics.
        print(json.dumps({'status': 'invalid-policy'}))
        raise SystemExit(2)


def main(argv=None, *, profile_root, store_factory, resolve_execution, execute_plan):
    # [seam] The consumer's trusted profile root and its bound store, authority resolver and executor.
    parser = SafeArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='operation', required=True)
    plan = sub.add_parser('plan', help='emit an advisory plan; grants no execution authority')
    plan.add_argument('--repo', required=True)
    plan.add_argument('--profile', required=True, help='logical profile ID from the trusted verification-profiles root')
    plan.add_argument('--base-sha', required=True)
    plan.add_argument('--family-id', required=True)
    plan.add_argument('--policy-checkpoint', required=True)
    plan.add_argument('--origin-policy', choices=['integration', 'task-completion'], default='integration')
    plan.add_argument('--task-command', action='append', default=[])
    run = sub.add_parser('run', help='execute only an already lifecycle-accepted integration plan')
    run.add_argument('--mode', choices=['integration'], required=True)
    run.add_argument('--repo', help='repository whose lifecycle authority is consulted')
    run.add_argument('--plan-id', help='exact accepted Verification Execution Plan identity')
    run.add_argument('--unit-id', help='optional exact execution unit within the accepted plan')
    run.add_argument('--failure-grant', action='append', default=[], metavar='GATE=GRANT',
                     help='reference an already issued signed grant for the exact gate')
    grant_import = sub.add_parser('grant-import',
        help='verify and store an externally signed critical-gate retry grant')
    grant_import.add_argument('--repo', required=True)
    grant_import.add_argument('--grant-file', required=True,
                              help='canonical signed grant envelope exported by the trusted HITL boundary')
    args = parser.parse_args(argv)
    if args.operation == 'grant-import':
        try:
            envelope = json.loads(pathlib.Path(args.grant_file).read_text(encoding='utf-8'))
            grant = store_factory(args.repo).import_failure_grant(envelope)
            print(json.dumps({'status': 'grant-imported', 'grant_id': grant['grant_id']},
                             sort_keys=True))
            return 0
        except (OSError, ValueError, StoreError) as exc:
            reason = str(exc)
            if not reason.startswith('FAILURE_GRANT_'):
                reason = 'FAILURE_GRANT_INVALID'
            code = exit_code('needs-human')
            print(json.dumps({'status': 'needs-human', 'reason_code': reason,
                              'exit_code': code}, sort_keys=True))
            return code
    if args.operation == 'run':
        if not args.plan_id:
            reason = 'VERIFICATION_EXECUTION_PLAN_REQUIRED'
            code = exit_code('verification-blocked')
            print(json.dumps({'status': 'verification-blocked', 'reason_code': reason,
                              'exit_code': code}, sort_keys=True))
            return code
        if not args.repo:
            reason = 'VERIFICATION_EXECUTION_PLAN_REQUIRED'
            code = exit_code('verification-blocked')
            print(json.dumps({'status': 'verification-blocked', 'reason_code': reason,
                              'exit_code': code}, sort_keys=True))
            return code
        try:
            failure_grants = {}
            for value in args.failure_grant:
                if '=' not in value:
                    raise ValueError('invalid-failure-grant-selector')
                gate_id, grant_id = value.split('=', 1)
                if not gate_id or not grant_id or gate_id in failure_grants:
                    raise ValueError('invalid-failure-grant-selector')
                failure_grants[gate_id] = grant_id
            record, profile, executable_plan, units = resolve_execution(
                pathlib.Path(args.repo), args.plan_id, unit_id=args.unit_id)
            result = execute_plan(pathlib.Path(args.repo), profile, executable_plan,
                                  store=store_factory(args.repo), sandbox_mode='required',
                                  attempt_id=f"g{record['lifecycle_generation']}-{args.plan_id[-16:]}",
                                  failure_grants=failure_grants, authority_context=record)
            response = result.to_record()
            response.update({'plan_id': args.plan_id,
                             'execution_units': [item['unit_id'] for item in units]})
            print(json.dumps(response, sort_keys=True))
            return exit_code(result.outcome) if exit_code(result.outcome) is not None else (
                0 if result.outcome == 'PASS' else 1)
        except (StoreError, OSError, ValueError) as exc:
            reason = str(exc) if str(exc) in {
                'VERIFICATION_EXECUTION_PLAN_REQUIRED', 'ACCEPTED_PLAN_UNAVAILABLE',
                'PLAN_BINDING_MISMATCH', 'SECRET_BEARING_CANDIDATE_UNSEALABLE',
                'CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE', 'CANDIDATE_SEALING_UNSAFE_OBJECT',
                'CANDIDATE_SEALING_SNAPSHOT_RACE'} else 'ACCEPTED_PLAN_UNAVAILABLE'
            code = exit_code('verification-blocked')
            print(json.dumps({'status': 'verification-blocked', 'reason_code': reason,
                              'exit_code': code}, sort_keys=True))
            return code
    try:
        # The CLI accepts only a logical ID. Resolving beneath this fixed
        # control-owned root prevents a task/provider/caller path from
        # selecting verification policy.
        if not isinstance(args.profile, str) or not args.profile.replace('-', '').replace('_', '').isalnum():
            raise InvalidPolicy('invalid-profile-id')
        trusted_root = pathlib.Path(profile_root)
        profile_path = (trusted_root / f'{args.profile}.json').resolve(strict=True)
        if profile_path.parent != trusted_root.resolve(strict=True):
            raise InvalidPolicy('profile-outside-trusted-root')
        profile = load_profile(profile_path, repository=pathlib.Path(args.repo))
        family = Family(args.family_id, args.base_sha, args.origin_policy, profile.content_hash, args.policy_checkpoint)
        result = build_plan(args.repo, profile, family, task_commands=args.task_command)
        print(json.dumps(result.to_record(), sort_keys=True))
        return 0
    except (InvalidPolicy, ValueError, OSError):
        print(json.dumps({'status': 'invalid-policy'}))
        return 2
