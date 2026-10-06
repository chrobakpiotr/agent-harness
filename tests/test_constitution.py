"""Versioned copies of the shared agent contract: `agent-harness constitution --check` detects any drift."""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (resources.files("agent_harness") / "constitution.md").read_bytes()


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agent_harness", "constitution", *args],
                          capture_output=True, check=False)


class ConstitutionCopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def check(self, content):
        path = self.dir / "copy.md"
        path.write_bytes(content)
        return cli("--check", str(path)).returncode

    def test_exact_copy_passes_and_any_drift_fails(self):
        self.assertEqual(0, self.check(CANONICAL))
        changed = CANONICAL.replace(b"Never", b"never", 1)
        self.assertNotEqual(CANONICAL, changed)
        for drifted in (changed, CANONICAL.rstrip(b"\n"), CANONICAL.replace(b"- Version: ", b"- Version: 9", 1)):
            with self.subTest(drifted=drifted[:0]):
                self.assertEqual(1, self.check(drifted))
        self.assertEqual(2, cli("--check", str(self.dir / "missing.md")).returncode)
        self.assertEqual((2, b""), (cli("--check", "").returncode, cli("--check", "").stdout))  # empty: never skipped
        self.assertEqual(1, self.check(CANONICAL + b"x" * 10_000))
        self.assertEqual(2, cli("--check", str(self.dir)).returncode)  # a directory is unreadable, not drifted

    def test_only_regular_files_are_read(self):
        fifo = self.dir / "fifo"
        os.mkfifo(fifo)
        proc = subprocess.run([sys.executable, "-m", "agent_harness", "constitution", "--check", str(fifo)],
                              capture_output=True, check=False, timeout=30)  # no writer: must not block
        self.assertEqual(2, proc.returncode)
        self.assertEqual(2, cli("--check", "/dev/zero").returncode)

    def test_repository_copy_is_the_shipped_text(self):
        proc = cli("--check", str(ROOT / "docs" / "agentic-sdd" / "constitution.md"))
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_print_and_digest_describe_the_shipped_text(self):
        self.assertEqual(CANONICAL, cli().stdout)
        version = re.search(rb"^- Version: (\S+)$", CANONICAL, re.MULTILINE).group(1).decode()
        digest = cli("--digest").stdout.decode().split()
        self.assertEqual(["constitution", version, "sha256:" + hashlib.sha256(CANONICAL).hexdigest()], digest)


if __name__ == "__main__":
    unittest.main()
