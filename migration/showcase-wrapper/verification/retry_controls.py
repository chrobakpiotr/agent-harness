"""Showcase's registered retry controls (AH5-08b): the critical PostgreSQL and RabbitMQ Gradle gates.

Consumer policy, verbatim from Showcase `49ab97f`: the registry constants from `verification/store.py` and the
Gradle evidence and init-script checks from `verification/supervisor.py`. The library checks the evidence against
the registration (`agent_harness.verification.retry`); see docs/migration/provenance.md.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shlex
import stat

from agent_harness.verification.retry import RetryControl
from agent_harness.verification.serialization import canonical
from agent_harness.verification.supervisor import SupervisorError

_RETRY_CONTROL_BINDINGS = {
    'critical-postgres-gate-disables-gradle-test-retry-v1': (
        'critical-postgres-regression', './tooling/scripts/verify-critical-postgres-tests.sh'),
    'critical-rabbit-gate-disables-gradle-test-retry-v1': (
        'critical-rabbitmq-regression', './tooling/scripts/verify-critical-rabbitmq-tests.sh'),
}
# The registered wrappers are protocol controls: any byte change requires an
# explicit review and a coordinated update to this trust anchor.
_RETRY_CONTROL_WRAPPER_SHA256 = {
    'critical-postgres-gate-disables-gradle-test-retry-v1':
        '7612e68ac5253b6c9afa36f173755fff7da7daf32a1c88297888d7231676aff0',
    'critical-rabbit-gate-disables-gradle-test-retry-v1':
        '0e31e2c66e193a606f1fa9b29f22115ebfe57cbbdee02af83f6aa8bc13bc04a7',
}
_RETRY_CONTROL_INPUT_SHA256 = {
    'critical-postgres-gate-disables-gradle-test-retry-v1': {
        'apps/ecommerce/backend/ecommerce.gradle':
            'e5bcb469e8099bf3908cd5eb5163dbd8e7243160ed5cd2b669f1bdcd92360144',
        'settings.gradle':
            'fc11261fbd15fbc313af5e7436b94f2fe81fd44036e71e0bd8004f62d2575e3c',
        'gradlew':
            'a5a5c199ba02189ae8c46a334223371a20599d9c298ef65e7540ede4a3f72d59',
        'gradle/wrapper/gradle-wrapper.jar':
            '497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7',
        'gradle/wrapper/gradle-wrapper.properties':
            '9edbaf00e02fb3e283499d0053f8b846fae30dd73ee83c3b01966d749a071414',
        'tooling/scripts/verify_critical_postgres_results.py':
            '5cbc89576231ea616f34aed03de3e12635470c4d0989110f1b2310296f8f91b8',
        'tooling/scripts/tests/test_verify_critical_postgres_results.py':
            '8a15f2bca8b9851c0b497c4c5b80e3a32813610168e82b0c30a8c55ec55a375d',
        'tooling/quality/critical-postgres-manifest.json':
            'd9e6489ca51c45e0f4787ed52b4fd95a773570e853257a19ca12bdbc69e035d7',
        'tooling/scripts/verify-critical-postgres-tests.sh':
            '7612e68ac5253b6c9afa36f173755fff7da7daf32a1c88297888d7231676aff0',
    },
    'critical-rabbit-gate-disables-gradle-test-retry-v1': {
        'apps/ecommerce/backend/ecommerce.gradle':
            'e5bcb469e8099bf3908cd5eb5163dbd8e7243160ed5cd2b669f1bdcd92360144',
        'settings.gradle':
            'fc11261fbd15fbc313af5e7436b94f2fe81fd44036e71e0bd8004f62d2575e3c',
        'gradlew':
            'a5a5c199ba02189ae8c46a334223371a20599d9c298ef65e7540ede4a3f72d59',
        'gradle/wrapper/gradle-wrapper.jar':
            '497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7',
        'gradle/wrapper/gradle-wrapper.properties':
            '9edbaf00e02fb3e283499d0053f8b846fae30dd73ee83c3b01966d749a071414',
        'tooling/scripts/verify_critical_rabbitmq_results.py':
            'b4e60bfbdc1375124007e4640bca4e768519b3c0bba31ce84da8dcc6fbdf4cbe',
        'tooling/scripts/tests/test_verify_critical_rabbitmq_results.py':
            'a8ad70be7eca5c4440007d9ae9b7d8c837de6c109667113222200c85ba228109',
        'tooling/quality/critical-rabbitmq-manifest.json':
            'eef30d457d829e4380ee02606440c3c98fdb94e1e35467563f573886b5284e27',
        'tooling/scripts/verify-critical-rabbitmq-tests.sh':
            '0e31e2c66e193a606f1fa9b29f22115ebfe57cbbdee02af83f6aa8bc13bc04a7',
    },
}
_REGISTERED_GRADLE_BUILD_INPUT_SHA256 = {
    'apps/ecommerce/frontend/ecommerce-frontend.gradle':
        '5519a9ffb311c31f9e4ac4312c4b31ee02e259406cc38511b68cd2b57de9cfa9',
    'build.gradle':
        'b2d5db7b604937ea0982c983d3b2dd912b5b9d910b308700193ddba986acd4df',
    'gradle.properties':
        '3094caed5e3cb5293956a90d26c10deb30b0b67ed73ee8457d56765f15487b94',
    'modules/adapters/ai/ai.gradle':
        '82767cb461f8e6f018c9a846c89ef2c5cd0b2c16b69480fae9dc27052bb96d91',
    'modules/adapters/amqp/amqp.gradle':
        '95ec9eecc3b5db709628e570f141a227e67377607c4b467f14da83e610b58aaa',
    'modules/adapters/aws/aws.gradle':
        '42d837cbd3662440be14bc6ee0aa2a5d70af4f03d68662d91a6bd854278b7531',
    'modules/adapters/camel/camel.gradle':
        '944eaa7967cce03fad3e45e5991883a64afe06a7ef6d1e38392ceae4301b81ac',
    'modules/adapters/common/common.gradle':
        'c0e696ac89bc50c22041bb8311f40e5abf8e4ca8071b50f36385fea55e941844',
    'modules/adapters/kafka/kafka.gradle':
        'b47aaf78883e14915c8bb10287ef6b6606df920b5ddee228816d034600286993',
    'modules/adapters/mail/mail.gradle':
        '7df1d90c3dbea5c773d6bd3b88a4060ec3b44b506bc4a934f2ee11b221cbce66',
    'modules/adapters/persistence/persistence.gradle':
        '94f28b7199de8c69dc7eb4d3c59c191a76afc7f59b4eebd6e665e7a9af92b877',
    'modules/adapters/security/security.gradle':
        '54ea133732d4674d9a7a4ce492b2a92e6b8247b749c6e6e3bc14207b34a2f253',
    'modules/adapters/web/web.gradle':
        '1d183240f0c0837cbdfbba40757b2f3e313af3a6d98eacce7852aefda6ecfb95',
    'modules/application/orchestration/orchestration.gradle':
        '702b65452be7645de5258230416a07014b6e58bba1a37fee107d8bfb08eec3f3',
    'modules/domain/domain.gradle':
        'c4ca4ef302255eb9f96f7096ce3bfd08c69abadbe0121e9c4760d3c47cd680d3',
    'modules/foundation/foundation.gradle':
        'dc90c0746b5470280d010340ebd99fe28c05dcda43283972c3b4b6e82c215d87',
    'tooling/quality/checkstyle/checkstyle.gradle':
        '19884067be7ab866a7e2b9e0a3ee579170b2123d33c0364863a2342ea61ecc44',
    'tooling/quality/dependencycheck/dependencycheck.gradle':
        'd4ed4f6cc0850bfa73b692181c6ce676c4dbc8bde0b05258ebfc5e36b34d1e94',
    'tooling/quality/jacoco/jacoco.gradle':
        'e96e9f997020f4bc0b3abb4a55c65083ae5fac055124dcf46448322acae082e9',
    'tooling/quality/pitest/pitest.gradle':
        'b2edb7c8bd45ed75de0688e3996ae7ca7641b6cecc6358f43281bf35290c5b06',
    'tooling/quality/pmd/pmd.gradle':
        'dc36240f3f2cc0747cab7f70a22e2e61b39440187b98deec6cce6617e1531122',
    'tooling/quality/spotbugs/spotbugs.gradle':
        'e5811f5965e988e159d5c1182e9f726514147fedcf20fa4fc27e3bc81a19578a',
    'tooling/quality/spotless/spotless-java.gradle':
        '7ca8fa7d074b04cae38fc8ab015e7010ef61910df01e8767a06032a1884ca2dc',
    'tooling/quality/spotless/spotless-prettier.gradle':
        '6298c46a770d41d3f1fe95c9338e3425f64473fd5958554b60f64decd369134d',
    'tooling/quality/spotless/spotless-xml.gradle':
        'fd52f6c94dc1d2ec193956f93cf133e6d6cb8be5d9206320357c17a69bdd7431',
}
for _retry_inputs in _RETRY_CONTROL_INPUT_SHA256.values():
    _retry_inputs.update(_REGISTERED_GRADLE_BUILD_INPUT_SHA256)
_NO_RETRY_COMMAND = 'python3 -c pass'


def _retry_control_inputs_digest(control_id):
    inputs = _RETRY_CONTROL_INPUT_SHA256.get(control_id)
    if inputs is None:
        return None
    return hashlib.sha256(canonical({'control_id': control_id,
                                     'inputs': dict(sorted(inputs.items()))})).hexdigest()


def _read_regular_worktree_file(root: pathlib.Path, relative: str) -> bytes:
    """Read a candidate policy input without traversing symlinks."""
    current = root
    info = None
    for component in pathlib.PurePosixPath(relative).parts:
        current = current / component
        try:
            info = current.lstat()
        except OSError:
            raise SupervisorError('retry-policy-violation') from None
        if stat.S_ISLNK(info.st_mode):
            raise SupervisorError('retry-policy-violation')
    if info is None or not stat.S_ISREG(info.st_mode):
        raise SupervisorError('retry-policy-violation')
    try:
        fd = os.open(current, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SupervisorError('retry-policy-violation')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                return stream.read()
        finally:
            os.close(fd)
    except OSError:
        raise SupervisorError('retry-policy-violation') from None


def _gradle_user_home_has_init_scripts(execution_run_dir: pathlib.Path) -> bool:
    home = execution_run_dir / 'verification-home' / 'gradle'
    try:
        for path in (execution_run_dir / 'verification-home', home):
            if path.is_symlink():
                return True
        if any((home / name).exists() or (home / name).is_symlink()
               for name in ('init.gradle', 'init.gradle.kts')):
            return True
        init_dir = home / 'init.d'
        if init_dir.is_symlink():
            return True
        if init_dir.exists():
            return not init_dir.is_dir() or next(init_dir.iterdir(), None) is not None
        return False
    except OSError:
        return True


def _registered_gradle_retry_evidence(worktree: pathlib.Path, control) -> dict:
    # The library supervisor has already matched the control's gate and command.
    controls, gate_id, command = (control.control_id,), control.gate_id, control.command
    try:
        root = pathlib.Path(worktree).resolve(strict=True)
        if not root.is_dir():
            raise ValueError()
    except (OSError, ValueError, RuntimeError):
        raise SupervisorError('retry-policy-violation') from None
    config_path = 'apps/ecommerce/backend/ecommerce.gradle'
    wrapper_path = command.removeprefix('./')
    try:
        registered_inputs = _RETRY_CONTROL_INPUT_SHA256[controls[0]]
        input_bytes = {relative: _read_regular_worktree_file(root, relative)
                       for relative in registered_inputs}
        if any(hashlib.sha256(input_bytes[path]).hexdigest() != expected
               for path, expected in registered_inputs.items()):
            raise SupervisorError('retry-policy-violation')
        config_bytes = input_bytes[config_path]
        wrapper_bytes = input_bytes[wrapper_path]
        if hashlib.sha256(wrapper_bytes).hexdigest() != _RETRY_CONTROL_WRAPPER_SHA256[controls[0]]:
            raise SupervisorError('retry-policy-violation')
        config = config_bytes.decode('utf-8')
        wrapper = wrapper_bytes.decode('utf-8')
    except (UnicodeDecodeError, SupervisorError):
        raise SupervisorError('retry-policy-violation') from None

    gate_property = 'criticalPostgresGate' if 'postgres' in controls[0] else 'criticalRabbitGate'
    enabled_name = 'criticalPostgresGateEnabled' if 'postgres' in controls[0] else 'criticalRabbitGateEnabled'
    expected_lines = (
        f'def {enabled_name} =',
        f"providers.gradleProperty('{gate_property}').getOrElse('false').toBoolean()",
        'def strictEvidenceGateEnabled = criticalPostgresGateEnabled || criticalRabbitGateEnabled',
        'maxRetries = strictEvidenceGateEnabled ? 0 : 2',
        'failOnPassedAfterRetry = strictEvidenceGateEnabled',
        'outputs.upToDateWhen { false }',
        'outputs.cacheIf { false }',
    )
    if any(config.count(line) != 1 for line in expected_lines):
        raise SupervisorError('retry-policy-violation')
    if (len(re.findall(r'\bretry\s*\{', config)) != 1 or
            len(re.findall(r'\bmaxRetries\b', config)) != 1 or
            len(re.findall(r'\bfailOnPassedAfterRetry\b', config)) != 1):
        raise SupervisorError('retry-policy-violation')

    gradle_files = []
    excluded = {'.git', '.gradle', 'build', 'node_modules', '.agent-runs', '.agent-state'}
    for parent, dirs, files in os.walk(root, followlinks=False):
        retained_dirs = []
        for name in dirs:
            if name in excluded:
                continue
            if (pathlib.Path(parent) / name).is_symlink():
                raise SupervisorError('retry-policy-violation')
            retained_dirs.append(name)
        dirs[:] = retained_dirs
        for name in files:
            if name.endswith('.gradle') or name.endswith('.gradle.kts'):
                path = pathlib.Path(parent) / name
                if path.is_symlink():
                    raise SupervisorError('retry-policy-violation')
                gradle_files.append(path)
                if len(gradle_files) > 512:
                    raise SupervisorError('retry-policy-violation')
    registered_gradle_files = {
        relative for relative in registered_inputs
        if relative.endswith(('.gradle', '.gradle.kts'))
    }
    discovered_gradle_files = {path.relative_to(root).as_posix() for path in gradle_files}
    if discovered_gradle_files != registered_gradle_files:
        raise SupervisorError('retry-policy-violation')
    config_file = root / config_path
    for path in gradle_files:
        if path == config_file:
            continue
        try:
            other = _read_regular_worktree_file(root, path.relative_to(root).as_posix()).decode('utf-8')
        except (OSError, UnicodeDecodeError, SupervisorError):
            raise SupervisorError('retry-policy-violation') from None
        if (re.search(r'\bretry\s*\{|\bmaxRetries\b|\bfailOnPassedAfterRetry\b', other) or
                'strictEvidenceGateEnabled' in other):
            raise SupervisorError('retry-policy-violation')

    gradle_property = f'-P{gate_property}'
    expected_gradle_invocations = 2 if 'postgres' in controls[0] else 1
    logical_wrapper = wrapper.replace('\\\r\n', ' ').replace('\\\n', ' ')
    gradle_commands = []
    forbidden_configuration_options = {
        '--init-script', '-I', '--settings-file', '-c', '--build-file', '-b',
        '--project-dir', '-p', '--include-build',
    }
    try:
        for line in logical_wrapper.splitlines():
            tokens = shlex.split(line, comments=True, posix=True)
            if './gradlew' not in tokens:
                continue
            if (not tokens or tokens[0] != './gradlew' or tokens.count('./gradlew') != 1 or
                    ':application:ecommerce:test' not in tokens):
                raise SupervisorError('retry-policy-violation')
            if any(token in forbidden_configuration_options or
                   token.startswith(('--init-script=', '--settings-file=', '--build-file=',
                                     '--project-dir=', '--include-build=', '-I', '-c', '-b', '-p'))
                   for token in tokens):
                raise SupervisorError('retry-policy-violation')
            properties = [token for token in tokens if token.startswith(gradle_property + '=')]
            if properties != [gradle_property + '=true']:
                raise SupervisorError('retry-policy-violation')
            gradle_commands.append(tokens)
    except ValueError:
        raise SupervisorError('retry-policy-violation') from None
    if len(gradle_commands) != expected_gradle_invocations:
        raise SupervisorError('retry-policy-violation')
    return {
        'schema_version': 1,
        'control_id': controls[0],
        'gate_id': gate_id,
        'command': command,
        'config_path': config_path,
        'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
        'wrapper_sha256': hashlib.sha256(wrapper_bytes).hexdigest(),
        'registered_inputs_sha256': _retry_control_inputs_digest(controls[0]),
        'strict_max_retries': 0,
        'fail_on_passed_after_retry': True,
    }


def _check_run_dir(backend_run_dir: pathlib.Path, environment) -> None:
    if environment and any(key in environment for key in
                           ('GRADLE_USER_HOME', 'GRADLE_OPTS', 'JAVA_OPTS')):
        raise SupervisorError('retry-policy-violation')
    if _gradle_user_home_has_init_scripts(backend_run_dir):
        raise SupervisorError('retry-policy-violation')


RETRY_CONTROLS = tuple(
    RetryControl(control_id, gate_id, command, _RETRY_CONTROL_WRAPPER_SHA256[control_id],
                 'apps/ecommerce/backend/ecommerce.gradle', _RETRY_CONTROL_INPUT_SHA256[control_id],
                 _registered_gradle_retry_evidence, _check_run_dir, run_namespace='registered-gradle')
    for control_id, (gate_id, command) in _RETRY_CONTROL_BINDINGS.items())
