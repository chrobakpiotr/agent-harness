"""Showcase wrapper over `agent_harness.verification.authority` (AH5-06).

Binds the Showcase lifecycle (`harness`), profile root, `showcase` profile and executable binder (Showcase's
`./gradlew` and profile-script conventions), which the library takes as explicit parameters.
"""
import pathlib

from agent_harness.verification import authority as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
PROFILES = pathlib.Path(__file__).resolve().parents[1] / 'verification-profiles'


def _lifecycle():
    import harness
    return harness


def _binder():
    from verification_command import executable_binding_for_command
    return executable_binding_for_command


def execution_plan_record(*args, **kwargs):
    kwargs.setdefault('executable_binder', _binder())
    return _impl.execution_plan_record(*args, **kwargs)


def publish_and_accept(repository, feature_dir, record, *, expected_generation):
    return _impl.publish_and_accept(repository, feature_dir, record, expected_generation=expected_generation,
                                    lifecycle=_lifecycle(), profile_root=PROFILES)


def resolve_accepted(repository, plan_id):
    return _impl.resolve_accepted(repository, plan_id, lifecycle=_lifecycle())


def resolve_execution(repository, plan_id, *, unit_id=None):
    return _impl.resolve_execution(repository, plan_id, lifecycle=_lifecycle(), profile_root=PROFILES,
                                   unit_id=unit_id, executable_binder=_binder())


def prepare_task_plan(repository, feature_dir, task_id, task_attempt, base_sha, task_commands):
    return _impl.prepare_task_plan(repository, feature_dir, task_id, task_attempt, base_sha, task_commands,
                                   lifecycle=_lifecycle(), profile_root=PROFILES, profile_id='showcase',
                                   executable_binder=_binder())


def validate_plan_record(record, *, repository=None, reconstruct=False):
    return _impl.validate_plan_record(record, profile_root=PROFILES, repository=repository, reconstruct=reconstruct,
                                      executable_binder=_binder())
