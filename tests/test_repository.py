"""RepoContext resolution against real disposable Git repositories and linked worktrees."""

import os
import pathlib
import subprocess
import tempfile
import unittest

from agent_harness.repository import RepositoryError, resolve_repo_context

GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def new_repo(path, commit=True):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    if commit:
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


class RepoContextTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name).resolve()
        self.repo = new_repo(self.tmp / "repo")

    def code(self, path):
        with self.assertRaises(RepositoryError) as caught:
            resolve_repo_context(path)
        return str(caught.exception)

    def test_linked_worktrees_share_authority_but_not_workspace(self):
        git(self.repo, "worktree", "add", "-q", "-b", "feature", str(self.tmp / "wt"))
        git(self.tmp / "wt", "commit", "-q", "--allow-empty", "-m", "feature")
        main, linked = resolve_repo_context(self.repo), resolve_repo_context(self.tmp / "wt")
        for key in ("repo_id", "authority_root", "evidence_root"):
            self.assertEqual(main[key], linked[key], key)
        self.assertEqual(str(self.repo / ".agent-runs" / "control" / "verification-v2"), main["authority_root"])
        self.assertEqual((str(self.repo), str(self.tmp / "wt")), (main["workspace"], linked["workspace"]))
        self.assertNotEqual(main["base_sha"], linked["base_sha"])

    def test_separate_repositories_never_share_authority(self):
        other = resolve_repo_context(new_repo(self.tmp / "other"))
        mine = resolve_repo_context(self.repo)
        self.assertNotEqual(mine["repo_id"], other["repo_id"])
        self.assertNotEqual(mine["authority_root"], other["authority_root"])

    def test_result_does_not_depend_on_cwd_or_entry_subdirectory(self):
        (self.repo / "a" / "b").mkdir(parents=True)
        expected = resolve_repo_context(self.repo)
        other = new_repo(self.tmp / "other")
        before = os.getcwd()
        self.addCleanup(os.chdir, before)
        os.chdir(other)
        self.assertEqual(expected, resolve_repo_context(self.repo / "a" / "b"))

    def test_resolution_is_read_only(self):
        resolve_repo_context(self.repo)
        self.assertFalse((self.repo / ".agent-runs").exists())

    def test_invalid_inputs_fail_closed(self):
        self.assertEqual("repository-unresolved", self.code(self.tmp / "missing"))
        (self.tmp / "plain").mkdir()
        self.assertEqual("repository-unresolved", self.code(self.tmp / "plain"))
        self.assertEqual("base-unresolved", self.code(new_repo(self.tmp / "unborn", commit=False)))
        git(self.tmp, "init", "-q", "--bare", "bare.git")
        self.assertEqual("bare-repository-unsupported", self.code(self.tmp / "bare.git"))

    def test_redirected_or_invalid_state_roots_fail_closed(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.repo / ".agent-runs").symlink_to(elsewhere)
        self.assertEqual("authority-root-symlink", self.code(self.repo))
        (self.repo / ".agent-runs").unlink()
        (self.repo / ".agent-runs").mkdir()
        (self.repo / ".agent-runs" / "control").symlink_to(elsewhere)
        self.assertEqual("authority-root-symlink", self.code(self.repo))
        (self.repo / ".agent-runs" / "control").unlink()
        (self.repo / ".agent-runs" / "control").write_text("not a directory")
        self.assertEqual("authority-root-invalid", self.code(self.repo))


if __name__ == "__main__":
    unittest.main()
