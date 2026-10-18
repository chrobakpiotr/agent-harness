"""Ported from Showcase `tooling/agent-harness/tests/test_verification_workspace.py` at `49ab97f` (AH5-08a)."""
import subprocess
import unittest

import test_verification_candidate as fixture

from agent_harness.verification.candidate import CandidateSealError, seal_candidate
from agent_harness.verification.workspace import WorkspaceError, materialize, validate


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        fixture.CandidateSealTests.setUp(self)
        self.root = self.root.resolve()  # macOS /var is a symlink; the store refuses symlinked ancestors
    tearDown = fixture.CandidateSealTests.tearDown
    # Reuse the real isolated Git fixture, not its candidate test methods.
    def create(self):
        self.bindings = {'task': 'T-001'}
        self.seal = seal_candidate(self.root, self.base, self.bindings)
        return materialize(self.root, self.seal, self.bindings,
                           family='family', attempt='1', execution='exec', policy='a' * 64)

    def test_full_source_and_runtime_outputs(self):
        (self.root / 'empty').mkdir()
        (self.root / 'ignored-output').mkdir()
        (self.root / 'ignored-output/input.txt').write_text('input\n')
        (self.root / 'src.txt').chmod(0o755)
        workspace = self.create()
        self.assertEqual('base\n', (workspace.source / 'src.txt').read_text())
        self.assertTrue((workspace.source / 'empty').is_dir())
        self.assertEqual(0o755, (workspace.source / 'src.txt').stat().st_mode & 0o777)
        self.assertTrue((workspace.source / 'ignored-output/input.txt').exists())
        (workspace.source / 'build').mkdir()
        (workspace.source / 'build/output.bin').write_bytes(b'\x00\xff')
        validate(workspace)
        self.assertFalse((self.root / 'build').exists())
        git = subprocess.run(['git', '-C', str(workspace.source), 'rev-parse', '--show-toplevel'], capture_output=True)
        self.assertNotEqual(0, git.returncode)

    def test_original_and_copy_drift_rejected(self):
        workspace = self.create()
        (workspace.source / 'src.txt').write_text('different\n')
        with self.assertRaises(WorkspaceError):
            validate(workspace)

    def test_original_baseline_drift_rejected(self):
        workspace = self.create()
        (self.root / 'src.txt').write_text('different\n')
        with self.assertRaises(WorkspaceError):
            validate(workspace)

    def test_baseline_privacy_before_copy(self):
        (self.root / 'src.txt').write_bytes(b'\x00\xff')
        subprocess.run(['git', 'add', 'src.txt'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'binary baseline'], cwd=self.root, check=True)
        self.base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        with self.assertRaises(CandidateSealError):
            self.create()
        self.assertFalse(any(self.root.glob('.agent-runs/**/source')))

    def test_collision_fails(self):
        self.create()
        with self.assertRaises(WorkspaceError):
            self.create()

    def test_runtime_symlink_rejected_before_resolve(self):
        (self.root / '.agent-runs').symlink_to(self.root / 'other', target_is_directory=True)
        with self.assertRaises((WorkspaceError, CandidateSealError)):
            self.create()

    def test_output_symlink_and_barrier_drift_rejected(self):
        workspace = self.create()
        (workspace.source / 'escape').symlink_to(self.root / 'src.txt')
        with self.assertRaises(WorkspaceError):
            validate(workspace)
        (workspace.source / 'escape').unlink()
        (workspace.source / '.git').write_text('gitdir: ../../../../.git\n')
        with self.assertRaises(WorkspaceError):
            validate(workspace)

    def test_baseline_secret_is_not_silently_excluded(self):
        (self.root / 'src.txt').write_text('password=secret-example\n')
        subprocess.run(['git', 'add', 'src.txt'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'secret baseline'], cwd=self.root, check=True)
        self.base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        with self.assertRaises(CandidateSealError):
            self.create()
        self.assertFalse(any(self.root.glob('.agent-runs/**/source')))

    def test_hardlink_special_and_unsafe_component_rejected(self):
        import os
        os.link(self.root / 'src.txt', self.root / 'linked.txt')
        with self.assertRaises((CandidateSealError, WorkspaceError)):
            self.create()
        (self.root / 'linked.txt').unlink()
        os.mkfifo(self.root / 'pipe')
        with self.assertRaises((CandidateSealError, WorkspaceError)):
            self.create()
        (self.root / 'pipe').unlink()
        seal = seal_candidate(self.root, self.base, {'task': 'T-001'})
        from agent_harness.verification.store import StoreError
        with self.assertRaises(StoreError):
            materialize(self.root, seal, {'task': 'T-001'}, family='../escape',
                        attempt='1', execution='exec', policy='a' * 64)

    def test_manifest_mutation_and_partial_workspace_reject(self):
        workspace = self.create()
        manifest = workspace.root / 'manifest.json'
        manifest.write_text('{}')
        with self.assertRaises(WorkspaceError):
            validate(workspace)
        manifest.unlink()
        with self.assertRaises(WorkspaceError):
            validate(workspace)
        with self.assertRaises(WorkspaceError):
            self.create()

    def test_source_race_rejects_before_copy(self):
        from unittest.mock import patch

        from agent_harness.verification import candidate
        original = candidate._read_worktree
        def changing(*args):
            result = original(*args)
            if args[1] == 'src.txt':
                (self.root / 'src.txt').write_text('concurrent edit\n')
            return result
        self.bindings = {'task': 'T-001'}
        seal = seal_candidate(self.root, self.base, self.bindings)
        with patch.object(candidate, '_read_worktree', side_effect=changing):
            with self.assertRaises((CandidateSealError, WorkspaceError)):
                materialize(self.root, seal, self.bindings, family='family', attempt='1',
                            execution='exec', policy='a' * 64)
        self.assertFalse(any(self.root.glob('.agent-runs/**/source')))

    def test_nested_git_metadata_is_not_copied(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested/.git').mkdir()
        (self.root / 'nested/.git/config').write_text('metadata\n')
        with self.assertRaises(WorkspaceError):
            self.create()
        self.assertFalse(any(self.root.glob('.agent-runs/**/source')))

    def test_manifest_symlink_and_hardlink_rejected(self):
        import os
        workspace = self.create()
        manifest = workspace.root / 'manifest.json'
        outside = self.root.parent / (self.root.name + '-external.json')
        outside.write_bytes(manifest.read_bytes())
        try:
            manifest.unlink()
            manifest.symlink_to(outside)
            with self.assertRaises(WorkspaceError):
                validate(workspace)
            manifest.unlink()
            os.link(outside, manifest)
            with self.assertRaises(WorkspaceError):
                validate(workspace)
        finally:
            outside.unlink()

    def test_descriptor_root_and_source_swaps_rejected(self):
        from dataclasses import replace
        workspace = self.create()
        with self.assertRaises(WorkspaceError):
            validate(replace(workspace, source=self.root))
        other = workspace.root.parent / 'other-execution'
        import shutil
        shutil.copytree(workspace.root, other)
        with self.assertRaises(WorkspaceError):
            validate(replace(workspace, root=other, source=other / 'source'))
