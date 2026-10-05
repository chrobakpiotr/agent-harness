"""Package smoke tests. Run them against the installed wheel (scripts/check-wheel.sh)."""

import os
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata

import agent_harness


def run_in_empty_dir(*args):
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, check=False)
        return proc, os.listdir(cwd)


class PackageTest(unittest.TestCase):
    def test_import_has_no_side_effects(self):
        proc, left_behind = run_in_empty_dir("-c", "import agent_harness")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertEqual(left_behind, [])

    def test_help_works_offline_from_empty_cwd(self):
        proc, left_behind = run_in_empty_dir("-m", "agent_harness", "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: agent-harness", proc.stdout)
        self.assertEqual(left_behind, [])

    def test_installed_metadata_matches_module_version(self):
        self.assertEqual(metadata.version("agent-harness"), agent_harness.__version__)


if __name__ == "__main__":
    unittest.main()
