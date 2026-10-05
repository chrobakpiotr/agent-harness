"""Changed surface on a real disposable repository: control state never counts as candidate change."""

import pathlib
import tempfile
import unittest

from agent_harness.verification.fingerprint import changed_surface
from agent_harness.verification.model import InvalidPolicy
from test_repository import git, new_repo


class ChangedSurfaceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = new_repo(pathlib.Path(tmp.name).resolve() / "repo")
        (self.repo / "src").mkdir()
        (self.repo / "src/a.py").write_text("a = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

    def test_changes_are_visible_and_control_state_is_excluded(self):
        (self.repo / "src/a.py").write_text("a = 2\n")
        (self.repo / "src/new.py").write_text("")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("")
        for state in (".agent-state", ".agent-runs/control/verification-v2"):
            (self.repo / state).mkdir(parents=True)
            (self.repo / state / "record.json").write_text("{}")
        surface = changed_surface(self.repo, self.base)
        self.assertEqual(surface.paths, (".gitignore", "ignored.txt", "src/a.py", "src/new.py"))
        self.assertEqual(surface.base_sha, self.base)

    def test_unknown_or_symbolic_base_is_rejected(self):
        for base in ("HEAD", "0" * 40, self.base[:12]):
            with self.subTest(base=base), self.assertRaises(InvalidPolicy):
                changed_surface(self.repo, base)


if __name__ == "__main__":
    unittest.main()
