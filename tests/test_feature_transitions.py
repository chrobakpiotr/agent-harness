"""AH5-14b-2 step 2: the transitions' consumer namespace.

The transitions themselves are verbatim and exercised end to end by Showcase's suites through its shim; these tests
pin the binding: what `bind` refuses, that the namespace is read at the moment of use, and that the three recovery
steps come only from the consumer.
"""
import unittest

from agent_harness import feature_transitions as ft


def complete_namespace(**overrides):
    names = {name: (lambda *a, **k: None) for name in ft.NAMESPACE_NAMES}
    names.update(overrides)
    return names


class BindTest(unittest.TestCase):
    def test_a_complete_namespace_binds(self):
        ns = ft.ModuleNamespace(complete_namespace())
        self.assertIs(ns, ft.bind(ns))

    def test_a_missing_name_refuses_before_any_operation(self):
        for name in ('write_packet', 'locked_state', '_authority_transaction', 'task_workspace_snapshot'):
            with self.subTest(name):
                names = complete_namespace()
                del names[name]
                with self.assertRaisesRegex(TypeError, f"lacks required names: \\['{name}'\\]"):
                    ft.bind(ft.ModuleNamespace(names))

    def test_the_recovery_bindings_must_be_callable(self):
        for name in ft.RECOVERY_BINDINGS:
            with self.subTest(name), self.assertRaisesRegex(TypeError, f"not callable: \\['{name}'\\]"):
                ft.bind(ft.ModuleNamespace(complete_namespace(**{name: None})))

    def test_the_recovery_bindings_are_exactly_the_three_mutating_steps(self):
        self.assertEqual(('task_workspace_snapshot', 'restore_task_workspace', 'restore_attempt_authorization'),
                         ft.RECOVERY_BINDINGS)
        for name in ft.RECOVERY_BINDINGS:
            self.assertIn(name, ft.NAMESPACE_NAMES)
            self.assertFalse(hasattr(ft, name), f'{name} has no library implementation (it stays the consumer\'s)')


class NamespaceTest(unittest.TestCase):
    def test_the_view_reads_each_name_at_the_moment_of_use(self):
        module_globals = {'value': 1}
        ns = ft.ModuleNamespace(module_globals)
        self.assertEqual(1, ns.value)
        module_globals['value'] = 2  # a later definition or a test's patch
        self.assertEqual(2, ns.value)
        with self.assertRaises(AttributeError):
            _ = ns.missing
        self.assertFalse(hasattr(ns, 'missing'))

    def test_the_namespace_resolves_a_recovery_binding_to_the_consumer_function(self):
        calls = []
        names = complete_namespace(restore_task_workspace=lambda *a, **k: calls.append((a, k)))
        ns = ft.bind(ft.ModuleNamespace(names))
        snapshot = {'head': 'h', 'files': {}}
        ns.restore_task_workspace('worktree', snapshot)
        self.assertEqual([(('worktree', snapshot), {})], calls)

    def test_moved_functions_take_the_namespace(self):
        import inspect
        moved = [name for name, value in vars(ft).items() if inspect.isfunction(value)
                 and value.__module__ == ft.__name__ and name != 'bind']
        self.assertEqual(80, len(moved))
        for name in moved:
            with self.subTest(name):
                parameter = inspect.signature(getattr(ft, name)).parameters.get('ns')
                self.assertIsNotNone(parameter)
                self.assertIs(inspect.Parameter.empty, parameter.default)  # required, never defaulted



class LegacyPacketReplanTest(unittest.TestCase):
    """A packet written for a task without `test_mode`/`test_seam` stores them as null; its own replan must accept it."""

    FIELDS = ('id', 'title', 'objective', 'role', 'agent_profile', 'depends_on', 'allowed_paths',
              'acceptance_criteria', 'risk_tags', 'verification', 'test_mode', 'test_seam')

    def setUp(self):
        import hashlib
        import pathlib

        def die(message):
            raise SystemExit(message)
        names = {'die': die, 'safe_task_id': lambda task_id: True, 'TASK_CONTRACT_FIELDS': self.FIELDS,
                 'sha256_bytes': lambda data: hashlib.sha256(data).hexdigest()}
        self.ns = ft.ModuleNamespace(names)
        names['semantic_task_contract'] = lambda *args, **kwargs: ft.semantic_task_contract(*args, ns=self.ns,
                                                                                            **kwargs)
        self.feature = pathlib.Path('F')

    def packet(self, *, test_policy='legacy', role='builder', stored_objective='o', **fields):
        task = {'id': 'T-1', 'title': 't', 'objective': 'o', 'role': role, 'depends_on': [], 'allowed_paths': [],
                'acceptance_criteria': ['AC-1'], 'risk_tags': [], 'verification': ['true'], **fields}
        stored = ft.semantic_task_contract_sha256(
            self.feature, {'feature': 'F'}, {**task, 'objective': stored_objective}, feature_sha256='f' * 64,
            test_policy=test_policy, ns=self.ns)
        # As the producer writes it (`packet_payload`): the profile defaults to the role; absent test fields are null.
        return {**{key: task[key] for key in self.FIELDS if key in task and key != 'id'}, 'feature': 'F',
                'task': 'T-1', 'feature_fingerprint': 'f' * 64, 'test_policy': test_policy, 'agent_profile': role,
                'test_mode': task.get('test_mode'), 'test_seam': task.get('test_seam'),
                'semantic_contract_sha256': stored}

    def bound(self, packet):
        return ft.packet_bound_semantic_contract_sha256(self.feature, packet, ns=self.ns)

    def test_a_legacy_task_without_test_fields_is_accepted(self):
        for policy, role in (('legacy', 'builder'), ('legacy', 'evaluator'), ('risk-driven', 'evaluator')):
            with self.subTest(policy=policy, role=role):
                packet = self.packet(test_policy=policy, role=role)
                self.assertEqual(packet['semantic_contract_sha256'], self.bound(packet))

    def test_a_task_with_its_test_fields_is_unchanged(self):
        packet = self.packet(test_policy='risk-driven', test_mode='existing-suite', test_seam='seam')
        self.assertEqual(packet['semantic_contract_sha256'], self.bound(packet))

    def test_where_the_policy_requires_them_null_is_not_absence(self):
        packet = self.packet(test_policy='risk-driven')
        with self.assertRaisesRegex(SystemExit, 'semantic contract fingerprint is inconsistent'):
            self.bound(packet)

    def test_a_packet_whose_contract_differs_from_its_hash_is_still_refused(self):
        with self.assertRaisesRegex(SystemExit, 'semantic contract fingerprint is inconsistent'):
            self.bound(self.packet(stored_objective='another objective'))


class SourceShapeTest(unittest.TestCase):
    """Static checks on the moved code: the declared names, and the three recovery calls exactly as the source makes them."""

    @classmethod
    def setUpClass(cls):
        import ast
        import inspect
        cls.ast = ast
        cls.tree = ast.parse(inspect.getsource(ft))

    def namespace_reads(self):
        ast = self.ast
        return {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name) and node.value.id == 'ns'}

    def test_the_declared_names_are_exactly_the_names_the_code_reads(self):
        self.assertEqual(set(ft.NAMESPACE_NAMES), self.namespace_reads())
        self.assertEqual(len(ft.NAMESPACE_NAMES), len(set(ft.NAMESPACE_NAMES)))

    def test_the_recovery_bindings_are_called_exactly_where_and_as_the_source_calls_them(self):
        ast = self.ast
        expected = {
            ('cmd_reopen', 'ns.task_workspace_snapshot(feature, tid)'),
            ('cmd_reopen', 'ns.restore_task_workspace(feature, tid, snapshots[tid])'),
            ('rollback_unexecuted_start', 'ns.restore_attempt_authorization(entry)'),
        }
        found = set()
        for function in self.tree.body:
            if isinstance(function, ast.FunctionDef):
                for node in ast.walk(function):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'ns'
                            and node.func.attr in ft.RECOVERY_BINDINGS):
                        found.add((function.name, ast.unparse(node)))
        self.assertEqual(expected, found)


if __name__ == '__main__':
    unittest.main()
