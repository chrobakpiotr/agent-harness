"""The installed package imports only stdlib, itself and its optional extras: never a consumer."""

import ast
import sys
import unittest
from pathlib import Path

import agent_harness

PACKAGE = Path(agent_harness.__file__).parent


class ImportBoundaryTest(unittest.TestCase):
    def test_only_stdlib_and_own_imports(self):
        allowed = set(sys.stdlib_module_names) | {"agent_harness", "cryptography"}
        foreign = set()
        for path in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                foreign |= {f"{path.name}: {n}" for n in names if n.split(".")[0] not in allowed}
        self.assertEqual(foreign, set())


class NoLocationDerivedRootTest(unittest.TestCase):
    def test_library_code_takes_roots_explicitly(self):
        # Only the CLI entry point may default anything from the process environment.
        offenders = [
            f"{path.name}: {needle}"
            for path in PACKAGE.rglob("*.py")
            if path.name != "__main__.py"
            for needle in ("__file__", "Path.cwd", "getcwd")
            if needle in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
