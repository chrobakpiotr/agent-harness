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
        f1 = {'observed_contract_binding', 'require_repository_identity', 'require_unchanged_contract',
              'require_attempt_contract', 'contract_binding_difference', 'verification_resume_identity',
              'cmd_invalidate_verification_resume'}  # F1's own additions, not moved code
        moved = [name for name, value in vars(ft).items() if inspect.isfunction(value)
                 and value.__module__ == ft.__name__ and name != 'bind' and name not in f1]
        self.assertEqual(80, len(moved))
        self.assertTrue(f1 <= set(vars(ft)))
        for name in moved:
            with self.subTest(name):
                parameter = inspect.signature(getattr(ft, name)).parameters.get('ns')
                self.assertIsNotNone(parameter)
                self.assertIs(inspect.Parameter.empty, parameter.default)  # required, never defaulted



class AttemptContractTest(unittest.TestCase):
    """F1: an attempt without a recorded binding, under each policy."""

    def ns(self):
        def die(message):
            raise SystemExit(message)
        return ft.ModuleNamespace({'die': die, 'canonical_json_sha256': lambda value: 'c' * 64})

    def test_without_a_contract_complete_keeps_the_optional_policy_and_complete_repair_refuses(self):
        absent = {'version': 1, 'contract': {'present': False}}
        self.assertIsNone(ft.require_attempt_contract('F', 'T-1', {'attempts': 1}, absent, ns=self.ns()))
        with self.assertRaisesRegex(SystemExit, 'VERIFICATION_CONTRACT_BINDING_MISSING: attempt 1 of T-1'):
            ft.require_attempt_contract('F', 'T-1', {'attempts': 1}, absent, unbound_without_contract=False,
                                        ns=self.ns())
        present = {'version': 1, 'contract': {'present': True, 'sha256': 'a' * 64}, 'inputs': {}}
        for strict in (True, False):
            with self.subTest(strict=strict), self.assertRaisesRegex(SystemExit, 'BINDING_MISSING'):
                ft.require_attempt_contract('F', 'T-1', {'attempts': 1}, present,
                                            unbound_without_contract=not strict, ns=self.ns())


class RepositoryIdentityTest(unittest.TestCase):
    def ns(self, root):
        def die(message):
            raise SystemExit(message)
        vc = type('vc', (), {'binding_repo_root': staticmethod(root),
                             'contract_binding': staticmethod(lambda path: ([], {'version': 1,
                                                                                 'contract': {'present': False}}))})
        return ft.ModuleNamespace({'die': die, 'vc': vc})

    def test_identity_is_checked_and_reported_apart_from_the_contract(self):
        def unconfirmed(path):
            raise OSError('feature is not in the repository')
        with self.assertRaisesRegex(SystemExit, '^VERIFICATION_REPOSITORY_UNCONFIRMED: feature is not in the repository'):
            ft.observed_contract_binding('F', ns=self.ns(unconfirmed))  # even without a contract
        self.assertEqual({'version': 1, 'contract': {'present': False}},
                         ft.observed_contract_binding('F', ns=self.ns(lambda path: path)))


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
