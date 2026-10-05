import pathlib
import subprocess
import tempfile
import unittest

from agent_harness.verification.candidate import CandidateSealError, seal_candidate


class CandidateSealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Candidate Test'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.email', 'candidate@example.invalid'], cwd=self.root, check=True)
        (self.root / '.gitignore').write_text('ignored-output/\n', encoding='utf-8')
        (self.root / 'src.txt').write_text('base\n', encoding='utf-8')
        subprocess.run(['git', 'add', '.gitignore', 'src.txt'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'base'], cwd=self.root, check=True)
        self.base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ignored_untracked_file_is_candidate_state_and_identity_is_stable(self):
        output = self.root / 'ignored-output' / 'report.json'
        output.parent.mkdir()
        output.write_text('{"ok":true}\n', encoding='utf-8')
        first = seal_candidate(self.root, self.base, {'plan_context': 'fixture-1'})
        second = seal_candidate(self.root, self.base, {'plan_context': 'fixture-1'})
        self.assertEqual(first, second)
        self.assertEqual('ignored-output/report.json', next(e['path'] for e in first.entries
                                                            if e['path'] == 'ignored-output/report.json'))
        report = next(e for e in first.entries if e['path'] == 'ignored-output/report.json')
        self.assertEqual(['untracked'], report['changes'])
        self.assertIn('worktree', report['layer_content_sha256'])

    def test_staged_and_worktree_layers_have_distinct_content_bindings(self):
        path = self.root / 'src.txt'
        path.write_text('staged value\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'src.txt'], cwd=self.root, check=True)
        path.write_text('worktree value\n', encoding='utf-8')
        sealed = seal_candidate(self.root, self.base, {'task': 'T-001', 'attempt': 2})
        entry = next(e for e in sealed.entries if e['path'] == 'src.txt')
        self.assertIn('staged', entry['changes'])
        self.assertIn('unstaged', entry['changes'])
        self.assertNotEqual(entry['layer_content_sha256']['staged'],
                            entry['layer_content_sha256']['worktree'])

    def test_secret_assignment_blocks_before_candidate_identity(self):
        (self.root / 'config.txt').write_text('api_key=example-secret-value\n', encoding='utf-8')
        with self.assertRaises(CandidateSealError) as caught:
            seal_candidate(self.root, self.base, {'task': 'T-001'})
        self.assertEqual('SECRET_BEARING_CANDIDATE_UNSEALABLE', caught.exception.reason)

    def test_high_entropy_token_blocks_sealing(self):
        (self.root / 'token.txt').write_text('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789\n', encoding='utf-8')
        with self.assertRaises(CandidateSealError) as caught:
            seal_candidate(self.root, self.base, {'task': 'T-001'})
        self.assertEqual('SECRET_BEARING_CANDIDATE_UNSEALABLE', caught.exception.reason)

    def test_post_seal_worktree_mutation_changes_identity(self):
        path = self.root / 'src.txt'
        path.write_text('first\n', encoding='utf-8')
        first = seal_candidate(self.root, self.base, {'task': 'T-001'})
        path.write_text('second\n', encoding='utf-8')
        second = seal_candidate(self.root, self.base, {'task': 'T-001'})
        self.assertNotEqual(first.candidate_identity, second.candidate_identity)
        self.assertNotEqual(first.changed_surface_id, second.changed_surface_id)


if __name__ == '__main__':
    unittest.main()
