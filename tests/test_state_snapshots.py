"""Ported from Showcase `tooling/agent-harness/tests/test_state_snapshots.py` at `49ab97f` (AH5-09).

Tests driven through Showcase's `harness.py` CLI stay in Showcase.
"""
import os
import pathlib
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from agent_harness import state_snapshots as snapshots


class StateSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=self.root, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/test/state-snapshot-fixture.git'],
                       cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '--allow-empty', '-q', '-m', 'base'], cwd=self.root, check=True)
        self.state_dir = self.root / '.agent-state'
        self.state_path = self.state_dir / 'SDD-OBS-001-test.json'

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, value, *, epoch='epoch-1', generation=1):
        return {'state_version': 2, 'feature': 'SDD-OBS-001', 'created_at': '2026-10-11T00:00:00Z',
                'lifecycle_epoch_id': epoch, 'feature_generation': generation, 'value': value}

    def commit(self, state, **kwargs):
        import json
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()
        return snapshots.commit_state_snapshot(
            self.root, self.state_dir, self.state_path, payload, state,
            **kwargs,
        )

    def test_commit_publishes_verifiable_pushable_snapshot_after_state_replacement(self):
        first = self.state('first')
        snapshot = self.commit(first)

        self.assertEqual(snapshot['commit'], snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))
        self.assertEqual(snapshot['commit'], snapshots.current_repository_snapshot_commit(self.root))
        self.assertEqual(snapshot['state_revision'], snapshots.sha256_bytes(self.state_path.read_bytes()))
        restored = snapshots.read_current_snapshot(self.root, 'SDD-OBS-001')
        self.assertEqual(self.state_path.read_bytes(), restored['files']['.agent-state/SDD-OBS-001-test.json'])
        self.assertEqual(1, restored['manifest']['feature_generation'])
        self.assertEqual('epoch-1', restored['manifest']['epoch_id'])
        self.assertTrue(snapshots.current_ref_exists(self.root, 'SDD-OBS-001'))

    def test_identical_full_tree_skips_snapshot_commit(self):
        state = self.state('stable')
        first = self.commit(state)
        with mock.patch.object(snapshots, '_commit_snapshot',
                               side_effect=AssertionError('unnecessary snapshot commit')):
            repeated = self.commit(state)
        self.assertEqual(first['commit'], repeated['commit'])
        self.assertEqual(first['snapshot_id'], repeated['snapshot_id'])

    def test_noop_fast_path_does_not_skip_changed_authority_file(self):
        state = self.state('stable')
        first = self.commit(state)
        (self.state_dir / 'audit').mkdir(parents=True)
        (self.state_dir / 'audit' / 'unexpected.json').write_bytes(b'changed')
        second = self.commit(state)
        self.assertNotEqual(first['commit'], second['commit'])
        self.assertEqual(b'changed', snapshots.read_current_snapshot(
            self.root, 'SDD-OBS-001')['files']['.agent-state/audit/unexpected.json'])

    def test_noop_fast_path_requires_exact_state_bytes(self):
        import json
        state = self.state('stable')
        first = self.commit(state)
        payload = json.dumps(state, separators=(',', ':')).encode('utf-8')
        second = snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                                 payload, state)
        self.assertNotEqual(first['commit'], second['commit'])
        self.assertEqual(payload, self.state_path.read_bytes())

    def test_noop_fast_path_repairs_missing_repository_restore_ref(self):
        state = self.state('stable')
        self.commit(state)
        subprocess.run(['git', 'update-ref', '-d', snapshots.current_repository_ref()],
                       cwd=self.root, check=True)
        self.assertIsNone(snapshots.current_repository_snapshot_commit(self.root))
        result = self.commit(state)
        self.assertEqual(result['commit'], snapshots.current_repository_snapshot_commit(self.root))

    def test_repository_identity_survives_a_clone_with_the_same_origin(self):
        clone = self.root.parent / (self.root.name + '-clone')
        subprocess.run(['git', 'clone', '-q', str(self.root), str(clone)], check=True)
        subprocess.run(['git', 'remote', 'set-url', 'origin',
                        'git@github.com:test/state-snapshot-fixture.git'], cwd=clone, check=True)
        self.assertEqual(snapshots._repo_identity(self.root), snapshots._repo_identity(clone))
        self.assertFalse(snapshots.current_ref_exists(self.root, 'SDD-OBS-001'))

    def test_crash_after_snapshot_before_state_replace_does_not_advance_current_ref(self):
        initial = self.commit(self.state('old'))
        old_bytes = self.state_path.read_bytes()

        def fail(boundary):
            if boundary == 'after-snapshot-publication':
                raise OSError('simulated crash')

        with self.assertRaisesRegex(OSError, 'simulated crash'):
            self.commit(self.state('new', generation=2), fault_injector=fail)

        self.assertEqual(old_bytes, self.state_path.read_bytes())
        self.assertEqual(initial['commit'], snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))

    def test_first_snapshot_crash_recognizes_legacy_state_without_previous_ref(self):
        import json
        legacy_path = self.state_dir / 'legacy-record.json'
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_bytes(b'legacy authority\n')
        self.state_path.write_bytes(json.dumps(self.state('legacy'), sort_keys=True).encode())
        prior_state = self.state_path.read_bytes()
        self.assertIsNone(snapshots.current_repository_snapshot_commit(self.root))

        def fail(boundary):
            if boundary == 'after-snapshot-publication':
                raise OSError('simulated first-save crash')

        with self.assertRaisesRegex(OSError, 'first-save crash'):
            self.commit(self.state('new', generation=2), fault_injector=fail)

        self.assertTrue(snapshots.recover_prepared_snapshots(self.root, self.state_dir))
        self.assertEqual(prior_state, self.state_path.read_bytes())
        self.assertEqual(b'legacy authority\n', legacy_path.read_bytes())
        self.assertFalse(snapshots.prepared_snapshot_exists(self.root))
        self.assertIsNone(snapshots.current_repository_snapshot_commit(self.root))

    def test_first_snapshot_crash_after_replacement_rolls_forward_legacy_state(self):
        import json
        legacy_path = self.state_dir / 'legacy-record.json'
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_bytes(b'legacy authority\n')
        self.state_path.write_bytes(json.dumps(self.state('legacy'), sort_keys=True).encode())
        self.assertIsNone(snapshots.current_repository_snapshot_commit(self.root))

        def fail(boundary):
            if boundary == 'after-state-replacement':
                raise OSError('simulated first-save replacement crash')

        with self.assertRaisesRegex(OSError, 'replacement crash'):
            self.commit(self.state('new', generation=2), fault_injector=fail)

        self.assertTrue(snapshots.recover_prepared_snapshots(self.root, self.state_dir))
        self.assertEqual('new', json.loads(self.state_path.read_bytes())['value'])
        self.assertEqual(b'legacy authority\n', legacy_path.read_bytes())
        self.assertEqual('consistent', snapshots.current_repository_state_status(self.root, self.state_dir))

    def test_crash_after_state_replace_does_not_acknowledge_new_snapshot(self):
        initial = self.commit(self.state('old'))
        old_ref = snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001')

        def fail(boundary):
            if boundary == 'after-state-replacement':
                raise OSError('simulated crash after state replacement')

        with self.assertRaisesRegex(OSError, 'after state replacement'):
            self.commit(self.state('new', generation=2), fault_injector=fail)

        self.assertEqual('new', __import__('json').loads(self.state_path.read_bytes())['value'])
        self.assertEqual(old_ref, snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))
        self.assertEqual(initial['commit'], old_ref)

    def test_prepared_snapshot_redoes_every_crash_boundary(self):
        import json
        self.commit(self.state('old'))
        boundaries = ('after-snapshot-publication', 'after-additional-files',
                      'after-state-replacement', 'after-archive-ref',
                      'after-feature-current-ref', 'after-repository-current-ref')
        generation = 2
        for index, boundary in enumerate(boundaries):
            state = self.state(f'new-{index}', generation=generation)
            payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()
            additional = {f'audit/crash-{index}.json': (f'entry-{index}'.encode(), 0o600)}

            def fail(at):
                if at == boundary:
                    raise OSError(f'simulated {boundary}')

            with self.assertRaisesRegex(OSError, boundary):
                snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                                 payload, state, additional_files=additional,
                                                 fault_injector=fail)
            try:
                snapshots.recover_prepared_snapshots(self.root, self.state_dir)
            except snapshots.SnapshotError as exc:
                raise AssertionError(f'recovery failed at {boundary}: {exc}') from exc
            if boundary == 'after-snapshot-publication':
                # No disk mutation occurred, so the unacknowledged redo record is discarded.
                self.assertEqual(f'new-{index - 1}' if index else 'old',
                                 json.loads(self.state_path.read_bytes())['value'])
            else:
                self.assertEqual(state, json.loads(self.state_path.read_bytes()))
                self.assertEqual(f'entry-{index}',
                                 (self.state_dir / f'audit/crash-{index}.json').read_text())
                self.assertEqual('consistent',
                                 snapshots.current_repository_state_status(self.root, self.state_dir))
            generation += 1

    def test_restore_refuses_while_prepared_snapshot_exists_even_if_state_was_deleted(self):
        import json
        self.commit(self.state('old'))
        state = self.state('new', generation=2)
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()

        def fail(boundary):
            if boundary == 'after-state-replacement':
                raise OSError('simulated crash')

        with self.assertRaisesRegex(OSError, 'simulated crash'):
            snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                             payload, state, fault_injector=fail)
        import shutil
        shutil.rmtree(self.state_dir)
        with self.assertRaisesRegex(snapshots.SnapshotError, 'restore-prepared-pending'):
            snapshots.restore_current_snapshot(self.root, 'SDD-OBS-001')

    def test_owner_discard_requires_exact_prepared_commit_and_audits_the_action(self):
        import json
        state = self.state('new')
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()

        def fail(boundary):
            if boundary == 'after-snapshot-publication':
                raise OSError('simulated crash')

        with self.assertRaisesRegex(OSError, 'simulated crash'):
            snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                             payload, state, fault_injector=fail)
        prepared = snapshots._prepared_refs(self.root)
        self.assertEqual(1, len(prepared))
        prepared_ref, feature, commit = prepared[0]
        self.assertFalse(self.state_dir.exists())

        with self.assertRaisesRegex(snapshots.SnapshotError, 'prepared-commit-stale'):
            snapshots.discard_prepared_snapshot(self.root, self.state_dir, feature,
                                                  '0' * len(commit), 'test-operator')
        self.assertEqual(commit, snapshots._prepared_refs(self.root)[0][2])

        result = snapshots.discard_prepared_snapshot(
            self.root, self.state_dir, feature, commit, 'test-operator')
        self.assertFalse(snapshots.prepared_snapshot_exists(self.root))
        self.assertFalse(self.state_dir.exists())
        audit_commit, audit_files = snapshots._read_commit(self.root, result['audit_commit'])
        self.assertEqual(result['audit_commit'], audit_commit)
        record = json.loads(audit_files['discard.json'])
        self.assertEqual('discard-prepared-snapshot', record['action'])
        self.assertEqual(prepared_ref, record['prepared_ref'])
        self.assertEqual(commit, record['prepared_commit'])
        self.assertEqual('test-operator', record['operator'])
        self.assertEqual(result['audit_ref'],
                         f"refs/agent-state-discarded/{feature}/{commit}")

        repeated = snapshots.discard_prepared_snapshot(
            self.root, self.state_dir, feature, commit, 'different-retry-operator')
        self.assertEqual(result['audit_commit'], repeated['audit_commit'])

    def test_owner_discard_refuses_when_state_root_exists(self):
        import json
        state = self.state('new')
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()

        def fail(boundary):
            if boundary == 'after-state-replacement':
                raise OSError('simulated crash')

        with self.assertRaisesRegex(OSError, 'simulated crash'):
            snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                             payload, state, fault_injector=fail)
        commit = snapshots._prepared_refs(self.root)[0][2]
        with self.assertRaisesRegex(snapshots.SnapshotError, 'prepared-state-present'):
            snapshots.discard_prepared_snapshot(
                self.root, self.state_dir, 'SDD-OBS-001', commit, 'test-operator')
        self.assertTrue(snapshots.prepared_snapshot_exists(self.root))


    def test_manifest_binds_state_revision_to_state_file_and_generation(self):
        self.commit(self.state('state-value', generation=7))
        snapshot = snapshots.read_current_snapshot(self.root, 'SDD-OBS-001')
        state_bytes = snapshot['files']['.agent-state/SDD-OBS-001-test.json']
        self.assertEqual(snapshots.sha256_bytes(state_bytes), snapshot['manifest']['state_revision'])
        self.assertEqual('SDD-OBS-001-test.json', snapshot['manifest']['state_path'])
        self.assertEqual(7, snapshot['manifest']['feature_generation'])

    def test_snapshot_publishes_immutable_recovery_record_with_state(self):
        recovery_path = self.state_dir / 'recovery-epochs' / 'epoch-1' / 'recovery-record.json'
        record = b'{"epoch_id":"epoch-1","operator":"test"}\n'
        state = self.state('recovered')
        import json
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()
        snapshots.commit_state_snapshot(
            self.root, self.state_dir, self.state_path, payload, state,
            additional_files={'recovery-epochs/epoch-1/recovery-record.json': (record, 0o600)},
        )
        self.assertEqual(record, recovery_path.read_bytes())
        saved = snapshots.read_current_snapshot(self.root, 'SDD-OBS-001')
        self.assertEqual(record, saved['files']['.agent-state/recovery-epochs/epoch-1/recovery-record.json'])

    def test_snapshot_preserves_lock_suffixed_authority_files(self):
        lock_named_record = self.state_dir / 'audit.lock'
        lock_named_record.parent.mkdir(parents=True)
        lock_named_record.write_bytes(b'authority record\n')
        self.commit(self.state('with-lock-named-record'))
        saved = snapshots.read_current_repository_snapshot(self.root)
        self.assertEqual(b'authority record\n', saved['files']['.agent-state/audit.lock'])
        self.assertEqual('consistent', snapshots.current_repository_state_status(self.root, self.state_dir))

    def test_snapshot_refuses_conflicting_immutable_recovery_record(self):
        recovery_path = self.state_dir / 'recovery-epochs' / 'epoch-1' / 'recovery-record.json'
        recovery_path.parent.mkdir(parents=True)
        recovery_path.write_bytes(b'{"different":true}\n')
        state = self.state('recovered')
        import json
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()
        with self.assertRaisesRegex(snapshots.SnapshotError, 'immutable-file-conflict'):
            snapshots.commit_state_snapshot(
                self.root, self.state_dir, self.state_path, payload, state,
                additional_files={'recovery-epochs/epoch-1/recovery-record.json':
                                  (b'{"epoch_id":"epoch-1"}\n', 0o600)},
            )
        self.assertFalse(self.state_path.exists())

    def test_retry_after_recovery_receipt_write_crash_is_idempotent(self):
        previous = self.commit(self.state('old'))
        previous_bytes = self.state_path.read_bytes()
        record = b'{"epoch_id":"epoch-2","operator":"test"}\n'
        state = self.state('new', epoch='epoch-2', generation=1)
        import json
        payload = (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()
        extra = {'recovery-epochs/epoch-2/recovery-record.json': (record, 0o600)}

        def fail(boundary):
            if boundary == 'after-additional-files':
                raise OSError('simulated recovery process crash')

        with self.assertRaisesRegex(OSError, 'recovery process crash'):
            snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                             payload, state, additional_files=extra,
                                             fault_injector=fail)
        self.assertEqual(previous_bytes, self.state_path.read_bytes())
        self.assertEqual(previous['commit'], snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))
        self.assertEqual(record, (self.state_dir / 'recovery-epochs/epoch-2/recovery-record.json').read_bytes())

        snapshots.commit_state_snapshot(self.root, self.state_dir, self.state_path,
                                        payload, state, additional_files=extra)
        self.assertEqual(state, json.loads(self.state_path.read_bytes()))

    def test_retention_keeps_current_and_first_snapshot_per_epoch(self):
        original_limit = snapshots.SNAPSHOT_LIMIT
        snapshots.SNAPSHOT_LIMIT = 3
        try:
            for index in range(4):
                self.commit(self.state(f'same-epoch-{index}', generation=index + 1))
            refs = snapshots._list_snapshot_refs(self.root, 'SDD-OBS-001')
            self.assertEqual(3, len(refs))
            current = snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001')
            retained = {commit for _ref, commit, _manifest in refs}
            self.assertIn(current, retained)
            self.assertEqual({'epoch-1'}, {manifest['epoch_id'] for _ref, _commit, manifest in refs})

            self.commit(self.state('epoch-two', epoch='epoch-2', generation=1))
            self.commit(self.state('epoch-three', epoch='epoch-3', generation=1))
            before = self.state_path.read_bytes()
            acknowledged = snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001')
            with self.assertRaisesRegex(snapshots.SnapshotError, 'retention-cap-reached'):
                self.commit(self.state('epoch-four', epoch='epoch-4', generation=1))
            self.assertEqual(before, self.state_path.read_bytes())
            self.assertEqual(acknowledged, snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))
        finally:
            snapshots.SNAPSHOT_LIMIT = original_limit

    def test_invalid_newest_snapshot_fails_closed_without_falling_back(self):
        self.commit(self.state('old'))
        newest = self.commit(self.state('new', generation=2))
        bad_ref = snapshots.current_ref('SDD-OBS-001')
        empty_tree = subprocess.check_output(['git', 'mktree'], cwd=self.root, input=b'').decode().strip()
        subprocess.run(['git', 'update-ref', bad_ref, empty_tree], cwd=self.root, check=True)

        self.assertTrue(snapshots.current_ref_exists(self.root, 'SDD-OBS-001'))
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.read_current_snapshot(self.root, 'SDD-OBS-001')
        self.assertNotEqual(newest['commit'], snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))

    def test_restore_requires_missing_state_and_restores_exact_tree(self):
        self.state_dir.mkdir()
        (self.state_dir / 'completion-records').mkdir()
        (self.state_dir / 'completion-records' / 'immutable.json').write_bytes(b'{"preserve":"exact"}\n')
        (self.state_dir / 'completion-records' / 'immutable.json').chmod(0o600)
        self.commit(self.state('active'))
        expected = {p.relative_to(self.state_dir).as_posix(): p.read_bytes()
                    for p in self.state_dir.rglob('*') if p.is_file()}
        os.replace(self.state_dir, self.root / 'saved-state')

        snapshots.restore_current_snapshot(self.root, 'SDD-OBS-001')

        actual = {p.relative_to(self.state_dir).as_posix(): p.read_bytes()
                  for p in self.state_dir.rglob('*') if p.is_file()}
        self.assertEqual(expected, actual)
        self.assertEqual(0o600, (self.state_dir / 'completion-records' / 'immutable.json').stat().st_mode & 0o777)

    def test_repository_snapshot_restores_all_features_from_latest_global_ref(self):
        first = self.commit(self.state('first-feature'))
        second_state = {'state_version': 2, 'feature': 'OTHER-FEATURE',
                        'created_at': '2026-10-11T00:00:00Z', 'lifecycle_epoch_id': 'epoch-other',
                        'feature_generation': 1, 'value': 'second-feature'}
        import json
        second_path = self.state_dir / 'OTHER-FEATURE.json'
        second_payload = (json.dumps(second_state, sort_keys=True, indent=2) + '\n').encode()
        second = snapshots.commit_state_snapshot(
            self.root, self.state_dir, second_path, second_payload, second_state)
        self.assertEqual(first['commit'], snapshots.current_snapshot_commit(self.root, 'SDD-OBS-001'))
        self.assertEqual(second['commit'], snapshots.current_repository_snapshot_commit(self.root))
        repository_snapshot = snapshots.read_current_repository_snapshot(self.root)
        self.assertEqual({'.agent-state/SDD-OBS-001-test.json', '.agent-state/OTHER-FEATURE.json'},
                         set(repository_snapshot['files']))

        expected = {path.relative_to(self.state_dir).as_posix(): path.read_bytes()
                    for path in self.state_dir.rglob('*') if path.is_file()}
        os.replace(self.state_dir, self.root / 'saved-state')
        snapshots.restore_current_snapshot(self.root, 'OTHER-FEATURE')
        actual = {path.relative_to(self.state_dir).as_posix(): path.read_bytes()
                  for path in self.state_dir.rglob('*') if path.is_file()}
        self.assertEqual(expected, actual)

    def test_restore_refuses_to_overwrite_existing_state(self):
        self.commit(self.state('active'))
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.restore_current_snapshot(self.root, 'SDD-OBS-001')

    def test_concurrent_lifecycle_commits_publish_a_complete_authority_tree(self):
        try:
            import fcntl
        except ImportError:
            self.skipTest('cross-process lifecycle lock requires fcntl')
        lock_path = snapshots.lifecycle_lock_path(self.root)
        first_snapshot_written = threading.Event()
        release_first = threading.Event()
        second_waiting = threading.Event()
        failures = []

        def state_bytes(value):
            import json
            state = self.state(value)
            return state, (json.dumps(state, sort_keys=True, indent=2) + '\n').encode()

        def first():
            state, payload = state_bytes('first')
            try:
                with lock_path.open('a+') as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    try:
                        snapshots.commit_state_snapshot(
                            self.root, self.state_dir, self.state_dir / 'first.json', payload, state,
                            fault_injector=lambda boundary: (first_snapshot_written.set(), release_first.wait(5))
                            if boundary == 'after-snapshot-publication' else None,
                        )
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except Exception as exc:  # surfaced in the owning test thread
                failures.append(exc)

        def second():
            state, payload = state_bytes('second')
            try:
                second_waiting.set()
                with lock_path.open('a+') as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    try:
                        snapshots.commit_state_snapshot(
                            self.root, self.state_dir, self.state_dir / 'second.json', payload, state)
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except Exception as exc:
                failures.append(exc)

        t1 = threading.Thread(target=first)
        t1.start()
        self.assertTrue(first_snapshot_written.wait(5))
        t2 = threading.Thread(target=second)
        t2.start()
        self.assertTrue(second_waiting.wait(5))
        time.sleep(0.05)
        self.assertTrue(t2.is_alive(), 'second commit must wait for the repository lifecycle lock')
        release_first.set()
        t1.join(10)
        t2.join(10)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual([], failures)

        current = snapshots.read_current_snapshot(self.root, 'SDD-OBS-001')
        self.assertEqual({'.agent-state/first.json', '.agent-state/second.json'}, set(current['files']))
        self.assertEqual(2, len(snapshots._list_snapshot_refs(self.root, 'SDD-OBS-001')))


if __name__ == '__main__':
    unittest.main()
