"""Real Showcase applicability and selected-input contract, independent of execution."""
import os
import pathlib
import subprocess
import tempfile
import unittest

from agent_harness.verification.authority import (
    execution_plan_record,
    validate_plan_record,
)
from agent_harness.verification.fingerprint import changed_surface
from agent_harness.verification.model import Family, Plan, Surface
from agent_harness.verification.planner import required_nodes
from agent_harness.verification.profile import load_profile, matches
from agent_harness.verification.store import StoreError

# Showcase profile at 50c18f9, copied as a test input: the package ships no built-in profile.
PROFILES = pathlib.Path(__file__).resolve().parent / 'fixtures' / 'verification-profiles'

BUILD = 'showcase-gradle-build'
PIT = 'domain-mutation-threshold'
PG = 'critical-postgres-regression'
RABBIT = 'critical-rabbitmq-regression'
DOCTOR = 'agentic-sdd-doctor'
HARNESS_TESTS = 'agent-harness-tests'
FRONTEND = 'showcase-frontend-build'
ALL = {BUILD, PIT, PG, RABBIT, DOCTOR, HARNESS_TESTS, FRONTEND}


class ShowcaseApplicabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile(PROFILES / 'showcase.json')
        cls.family = Family('applicability', 'a' * 40, 'integration',
                            cls.profile.content_hash, 'b' * 64, 'c' * 64, 'd' * 64)

    def nodes(self, paths):
        surface = Surface(tuple(paths), (), tuple(paths), 'fixture', self.family.base_sha)
        return required_nodes(self.profile, self.family, surface)

    def test_real_profile_truth_table_and_selected_inputs(self):
        cases = {
            'modules/adapters/persistence/src/main/java/example/Repository.java': {BUILD, PG},
            'modules/adapters/persistence/persistence.gradle': {BUILD, PG},
            'modules/adapters/persistence/src/main/resources/db/migration/V002__orders.sql': {BUILD, PG},
            'modules/adapters/amqp/src/main/java/example/Publisher.java': {BUILD, RABBIT},
            'modules/adapters/amqp/amqp.gradle': {BUILD, RABBIT},
            'modules/domain/src/main/java/example/Order.java': {BUILD, PIT, PG, RABBIT},
            'modules/foundation/src/main/java/example/Validation.java': {BUILD, PIT, PG, RABBIT},
            'modules/foundation/foundation.gradle': {BUILD, PIT, PG, RABBIT},
            'modules/application/orchestration/src/main/java/com/cp/ecommerce/application/shipment/ShipmentWorkflow.java': {BUILD, PG, RABBIT},
            'apps/ecommerce/backend/src/main/java/example/UseCase.java': {BUILD, PG, RABBIT},
            'apps/ecommerce/frontend/ecommerce-frontend.gradle': {BUILD, FRONTEND},
            'apps/ecommerce/frontend/karma.conf.js': {FRONTEND},
            'apps/ecommerce/frontend/eslint.config.js': {FRONTEND},
            'apps/ecommerce/backend/src/main/resources/application.yml': {BUILD, PG, RABBIT},
            'apps/ecommerce/backend/ecommerce.gradle': {BUILD, PG, RABBIT},
            'apps/ecommerce/backend/src/test/resources/application-test.properties': {BUILD, PG, RABBIT},
            'tooling/quality/pitest/pitest.gradle': {BUILD, PIT},
            'tooling/scripts/verify-domain-pitest.sh': {PIT},
            'tooling/scripts/verify_pit_report.py': {PIT},
            'tooling/scripts/tests/test_verify_pit_report.py': {PIT},
            'tooling/scripts/verify-critical-postgres-tests.sh': {PG},
            'tooling/scripts/verify_critical_postgres_results.py': {PG},
            'tooling/scripts/tests/test_verify_critical_postgres_results.py': {PG},
            'tooling/quality/critical-postgres-manifest.json': {PG},
            'tooling/scripts/verify-critical-rabbitmq-tests.sh': {RABBIT},
            'tooling/scripts/verify_critical_rabbitmq_results.py': {RABBIT},
            'tooling/scripts/tests/test_verify_critical_rabbitmq_results.py': {RABBIT},
            'tooling/quality/critical-rabbitmq-manifest.json': {RABBIT},
            'docs/guide.md': {DOCTOR},
            'README.md': {DOCTOR},
            'docs/specs/SDD-OBS-001/spec.md': {DOCTOR, HARNESS_TESTS},
        }
        for path in ('gradlew', 'gradlew.bat', 'build.gradle', 'build.gradle.kts', 'settings.gradle',
                     'settings.gradle.kts', 'gradle.properties',
                     'gradle/wrapper/gradle-wrapper.properties',
                     'tooling/agent-harness/verification-profiles/showcase.json'):
            cases[path] = ALL
        for path, expected in cases.items():
            with self.subTest(path=path):
                nodes = self.nodes((path,))
                self.assertEqual(expected, {n.profile_gate_id for n in nodes})
                for node in nodes:
                    self.assertTrue(any(matches(pattern, path) for pattern in node.gate.inputs),
                                    (node.id, path))

    def test_noop_is_empty_advisory_and_cannot_publish_executable_plan(self):
        self.assertEqual((), tuple(self.nodes(())))
        record = execution_plan_record('fixture', 'f' * 64, 1, 'showcase', self.profile,
            Plan(self.family, ()), task_id='T-1', task_attempt=1, origin_binding='integration')
        with self.assertRaises(StoreError):
            validate_plan_record(record, profile_root=PROFILES)

    def test_real_git_deletion_and_rename_retain_source_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            env = dict(os.environ, GIT_AUTHOR_NAME='test', GIT_AUTHOR_EMAIL='test@example.invalid',
                       GIT_COMMITTER_NAME='test', GIT_COMMITTER_EMAIL='test@example.invalid')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            persistence = root / 'modules/adapters/persistence/src/main/java/example/Repository.java'
            amqp = root / 'modules/adapters/amqp/src/main/java/example/Publisher.java'
            for path in (persistence, amqp):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original\n')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'base'], check=True, env=env)
            base = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            persistence.unlink()
            renamed = root / 'docs/moved.java'
            renamed.parent.mkdir()
            amqp.rename(renamed)
            subprocess.run(['git', '-C', str(root), 'add', '-A'], check=True)
            surface = changed_surface(root, base)
            self.assertIn(persistence.relative_to(root).as_posix(), surface.deleted)
            self.assertIn(amqp.relative_to(root).as_posix(), surface.paths)
            nodes = required_nodes(self.profile, self.family, surface)
            self.assertTrue({BUILD, PG, RABBIT} <= {n.profile_gate_id for n in nodes})
