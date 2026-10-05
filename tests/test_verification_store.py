import json
import os
import pathlib
import sys
import subprocess
import threading
import tempfile
import unittest

from agent_harness.verification.store import (StoreError, VerificationStore, publish_create_once, resolve_control_root,
                                ReconciliationOutcome)


class StoreTest(unittest.TestCase):
    def test_create_once_is_idempotent_and_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp).resolve() / 'immutable.json'
            first = publish_create_once(path, {'x': 1})
            self.assertEqual(first, publish_create_once(path, {'x': 1}))
            with self.assertRaisesRegex(StoreError, 'immutable-record-collision'):
                publish_create_once(path, {'x': 2})

    def test_unresolved_execution_is_repository_admission_barrier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            started = store.executions / 'exec-1' / 'started.json'
            started.parent.mkdir(parents=True)
            started.write_text(json.dumps({'schema_version': 2, 'execution_id': 'exec-1',
                                           'repository_id': store.repository_id, 'backend': 'test',
                                           'worktree': str(root), 'launch_intent_hash': 'a' * 64,
                                           'started_at': 1.0, 'family_id': 'f', 'attempt_id': 'a', 'gate_id': 'g'}))
            with self.assertRaisesRegex(StoreError, 'verification-owned'):
                store.admit_repository_verification()

    def test_admission_validates_identity_and_terminal_publication_is_create_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            started = store.executions / 'exec-1' / 'started.json'
            started.parent.mkdir(parents=True)
            started.write_text(json.dumps({'schema_version': 2, 'execution_id': 'other', 'backend': 'b'}))
            (started.parent / 'drained.json').write_text(json.dumps({
                'schema_version': 2, 'execution_id': 'exec-1', 'backend': 'b', 'status': 'drained'}))
            with self.assertRaisesRegex(StoreError, 'invalid-execution-history'):
                store.admit_repository_verification()

    def test_critical_failures_ignore_unbound_evidence_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            path = store.runs / 'f' / 'g' / 'terminal.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'repository_id':'r','profile_hash':'p','gate_id':'g',
                'pre_fingerprint':'f','status':'verification-failed','critical':True,'evidence_id':'e'}))
            self.assertEqual([], store.critical_failures(
                repository_id=store.repository_id, profile_hash='p', gate_id='g', fingerprint='f'))

    def test_malformed_execution_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            path = store.executions / 'exec-1' / 'started.json'
            path.parent.mkdir(parents=True)
            path.write_text('{')
            with self.assertRaisesRegex(StoreError, 'invalid-execution-history'):
                store.admit_repository_verification()

    def test_primary_repository_authority_resolution(self):
        # The live checkout, primary or linked worktree, resolves to one primary store.
        root = pathlib.Path(__file__).resolve().parents[1]
        control, repository_id = resolve_control_root(root)
        self.assertEqual('.agent-runs/control/verification-v2', str(control.relative_to(control.parents[2])))
        self.assertEqual(64, len(repository_id))

    def test_canonical_identity_is_shared_by_real_linked_worktrees_only(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            main = base / 'repo'
            main.mkdir()
            subprocess.run(['git', 'init', str(main)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(['git', '-C', str(main), 'config', 'user.email', 'test@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(main), 'config', 'user.name', 'Test'], check=True)
            (main / 'tracked').write_text('x')
            subprocess.run(['git', '-C', str(main), 'add', 'tracked'], check=True)
            subprocess.run(['git', '-C', str(main), 'commit', '-m', 'init'], check=True, stdout=subprocess.DEVNULL)
            linked = base / 'linked'
            subprocess.run(['git', '-C', str(main), 'worktree', 'add', str(linked)], check=True, stdout=subprocess.DEVNULL)
            main_control, main_id = resolve_control_root(main)
            linked_control, linked_id = resolve_control_root(linked)
            self.assertEqual((main_control, main_id), (linked_control, linked_id))
            stale_local = linked / '.agent-runs' / 'control' / 'verification-v2'
            stale_local.mkdir(parents=True)
            (stale_local / 'authority.json').write_text('stale-local-authority')
            re_resolved_control, re_resolved_id = resolve_control_root(linked)
            self.assertEqual((main_control, main_id), (re_resolved_control, re_resolved_id))
            self.assertNotEqual(stale_local.resolve(), re_resolved_control)
            unrelated = base / 'other'
            unrelated.mkdir()
            subprocess.run(['git', 'init', str(unrelated)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(['git', '-C', str(unrelated), 'config', 'user.email',
                            'test@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(unrelated), 'config', 'user.name',
                            'Test'], check=True)
            (unrelated / 'tracked').write_text('x')
            subprocess.run(['git', '-C', str(unrelated), 'add', 'tracked'], check=True)
            subprocess.run(['git', '-C', str(unrelated), 'commit', '-m', 'init'], check=True, stdout=subprocess.DEVNULL)
            _, unrelated_id = resolve_control_root(unrelated)
            self.assertNotEqual(main_id, unrelated_id)

    def test_create_once_write_failure_never_leaves_valid_authority(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp).resolve() / 'authority.json'
            real_fdopen = __import__('os').fdopen
            class BrokenStream:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def write(self, payload): raise OSError('injected')
                def flush(self): pass
                def fileno(self): return 1
            with mock.patch('agent_harness.verification.store.os.fdopen', return_value=BrokenStream()):
                with self.assertRaisesRegex(StoreError, 'publication-failed'):
                    publish_create_once(path, {'authority': True})
            self.assertFalse(path.exists())

    def test_repository_admission_reconciles_only_proven_drained_and_rescans(self):
        from agent_harness.verification.store import ReconciliationOutcome
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            journal = store.executions / 'exec-1'
            journal.mkdir(parents=True)
            (journal / 'started.json').write_text(json.dumps({
                'schema_version': 2, 'execution_id': 'exec-1', 'repository_id': store.repository_id,
                'backend': 'test', 'worktree': str(root), 'launch_intent_hash': 'a' * 64,
                'started_at': 1.0, 'family_id': 'family', 'attempt_id': 'attempt', 'gate_id': 'gate'}))
            outcome = store.admit_repository_verification(lambda _record: ReconciliationOutcome.PROVEN_DRAINED)
            self.assertIsNone(outcome)
            self.assertTrue((journal / 'drained.json').exists())
            fresh = VerificationStore(root, control_root=root / 'store')
            fresh.admit_repository_verification()

    def test_repository_admission_keeps_active_and_uncertain_unresolved(self):
        from agent_harness.verification.store import ReconciliationOutcome
        for outcome in (ReconciliationOutcome.STILL_ACTIVE, ReconciliationOutcome.UNCERTAIN):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                store = VerificationStore(root, control_root=root / 'store')
                journal = store.executions / 'exec-1'
                journal.mkdir(parents=True)
                (journal / 'started.json').write_text(json.dumps({
                    'schema_version': 2, 'execution_id': 'exec-1', 'repository_id': store.repository_id,
                    'backend': 'test', 'worktree': str(root), 'launch_intent_hash': 'a' * 64,
                    'started_at': 1.0, 'family_id': 'family', 'attempt_id': 'attempt', 'gate_id': 'gate'}))
                with self.assertRaisesRegex(StoreError, 'verification-owned'):
                    store.admit_repository_verification(lambda _record: outcome)
                self.assertFalse((journal / 'drained.json').exists())

    def test_unrelated_projection_never_creates_terminal_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            projection = store.runs / 'family' / 'evidence' / 'projection.json'
            projection.parent.mkdir(parents=True)
            projection.write_text(json.dumps({'status': 'pass', 'evidence_id': 'evidence'}))
            with self.assertRaisesRegex(StoreError, 'projection-without-terminal'):
                list(store.iter_evidence())

    def test_identical_and_conflicting_create_once_races_have_one_authority(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp).resolve() / 'race.json'
            barrier = threading.Barrier(2)
            def publish(value):
                barrier.wait()
                try:
                    return publish_create_once(path, value)
                except StoreError as exc:
                    return str(exc)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(publish, ({'v': 1}, {'v': 2})))
            self.assertEqual(1, sum(isinstance(value, str) and len(value) == 64 for value in results))
            self.assertEqual(1, results.count('immutable-record-collision'))
            self.assertIn(json.loads(path.read_text()), ({'v': 1}, {'v': 2}))

    def test_red_store_requires_atomic_reservation_and_terminal_reconstruction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            self.assertTrue(hasattr(store, 'admit_and_reserve'))
            self.assertTrue(hasattr(store, 'reconstruct_terminals'))

    def test_red_store_rejects_hostile_execution_paths_and_symlink_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            store = VerificationStore(root, control_root=root / 'store')
            store.executions.mkdir(parents=True)
            with self.assertRaisesRegex(StoreError, 'invalid-execution-id'):
                store.admit_and_reserve('../escape', {'execution_id': '../escape',
                    'repository_id': store.repository_id, 'schema_version': 2})

    def test_red_repository_lock_serializes_separate_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            control = root / 'control'
            script = "import pathlib,sys,time; sys.path.insert(0,sys.argv[1]); from agent_harness.verification.store import repository_lock; p=pathlib.Path(sys.argv[2]);\nwith repository_lock(p,timeout=2):\n p.joinpath('entered').write_text(str(__import__('time').time())); time.sleep(.35)\n"
            package = pathlib.Path(__file__).resolve().parents[1]
            processes = [subprocess.Popen([sys.executable, '-c', script, str(package), str(control)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
            results = [process.communicate(timeout=5) for process in processes]
            self.assertEqual([0, 0], [process.returncode for process in processes], results)

    def test_terminal_receipt_is_authority_and_projection_is_rebuilt_after_restart(self):
        from agent_harness.verification.model import Evidence
        from agent_harness.verification.serialization import digest, evidence_record
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            record = {'schema_version': 2, 'evidence_id': 'e1', 'family_id': 'f1',
                'ownership_token': 'owner', 'gate_id': 'g1', 'repository_id': store.repository_id,
                'profile_hash': 'a' * 64, 'policy_checkpoint': 'b' * 40, 'command_hash': 'c' * 64,
                'candidate_identity': 'e' * 64, 'final_changed_surface_id': 'f' * 64,
                'origin_policy': 'integration', 'pre_fingerprint': 'd' * 64, 'post_fingerprint': 'd' * 64,
                'sandbox': 'off', 'retry_policy': 'forbid', 'process_invocations': 1, 'exit_code': 0,
                'started_at': 1.0, 'ended_at': 2.0, 'status': 'pass', 'artifacts': [], 'dependencies': []}
            record['receipt_hash'] = digest(record)
            terminal = store.runs / 'f1' / 'e1' / 'terminal.json'
            publish_create_once(terminal, record)
            fresh = VerificationStore(root, control_root=root / 'store')
            found = fresh.reconstruct_terminals()
            self.assertEqual(['e1'], [item['evidence_id'] for item in found])
            projection = terminal.parent / 'projection.json'
            self.assertTrue(projection.exists())
            self.assertEqual('e1', json.loads(projection.read_text())['evidence_id'])

    def test_terminal_without_projection_survives_projection_failure_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            terminal = {'schema_version': 2, 'evidence_id': 'eid', 'family_id': 'fam',
                'ownership_token': 'owner', 'gate_id': 'gate', 'repository_id': store.repository_id,
                'profile_hash': 'a' * 64, 'policy_checkpoint': 'b' * 40, 'command_hash': 'c' * 64,
                'candidate_identity': 'e' * 64, 'final_changed_surface_id': 'f' * 64,
                'origin_policy': 'integration', 'pre_fingerprint': 'd' * 64, 'post_fingerprint': 'd' * 64,
                'sandbox': 'off', 'retry_policy': 'forbid', 'process_invocations': 1, 'exit_code': 0,
                'started_at': 1.0, 'ended_at': 2.0, 'status': 'pass', 'artifacts': [], 'dependencies': []}
            from agent_harness.verification.serialization import digest
            terminal['receipt_hash'] = digest(terminal)
            path = store.runs / 'fam' / 'eid' / 'terminal.json'
            publish_create_once(path, terminal)
            def fail(boundary):
                if boundary == 'projection-before-replace': raise OSError('injected')
            store._fault_injector = fail
            with self.assertRaises(OSError): store.reconstruct_terminals()
            self.assertTrue(path.exists())
            fresh = VerificationStore(root, control_root=root / 'store')
            self.assertEqual(['eid'], [record['evidence_id'] for record in fresh.reconstruct_terminals()])

    def test_authoritative_publication_fault_points_never_leave_partial_receipt(self):
        boundaries = ('before-publication', 'during-write', 'before-file-fsync',
                      'before-atomic-publication', 'before-directory-fsync')
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp).resolve()
                store = VerificationStore(root, control_root=root / 'control')
                store.executions.mkdir(parents=True)
                journal = store.executions / 'exec-fault'
                journal.mkdir()
                path = journal / 'started.json'
                record = {'schema_version': 2, 'execution_id': 'exec-fault',
                    'repository_id': store.repository_id, 'backend': 'test', 'worktree': str(root),
                    'family_id': 'f', 'attempt_id': 'a', 'gate_id': 'g',
                    'launch_intent_hash': 'a' * 64, 'started_at': 1.0}
                def fail(current):
                    if current == boundary: raise OSError('injected')
                with self.assertRaises(StoreError):
                    publish_create_once(path, record, fault=fail)
                fresh = VerificationStore(root, control_root=root / 'control')
                if boundary == 'before-directory-fsync':
                    self.assertTrue(path.exists())
                    with self.assertRaises(StoreError): fresh.admit_repository_verification()
                else:
                    self.assertFalse(path.exists())
                    # No durable STARTED record means the launch barrier was
                    # never crossed; the empty journal cannot describe a live
                    # payload and admission remains recoverable.
                    fresh.admit_repository_verification()

    def test_atomic_reservation_is_repository_wide_and_fresh_store_sees_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            record = {'schema_version': 2, 'execution_id': 'exec-a', 'repository_id': store.repository_id,
                'backend': 'phase-a-test', 'worktree': str(root), 'family_id': 'f', 'attempt_id': 'a',
                'gate_id': 'g', 'launch_intent_hash': 'a' * 64, 'started_at': 1.0}
            journal = store.admit_and_reserve('exec-a', record)
            fresh = VerificationStore(root, control_root=root / 'store')
            self.assertTrue((journal / 'started.json').exists())
            self.assertEqual('exec-a', fresh._scan_executions()[0][1]['execution_id'])
            with self.assertRaisesRegex(StoreError, 'verification-owned'):
                fresh.admit_and_reserve('exec-b', {**record, 'execution_id': 'exec-b'})

    def test_malformed_and_unsupported_journal_authority_fails_closed(self):
        for raw in ('{', '{"schema_version":99}', json.dumps({'schema_version': 2})):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp).resolve()
                store = VerificationStore(root, control_root=root / 'store')
                directory = store.executions / 'exec-x'
                directory.mkdir(parents=True)
                (directory / 'started.json').write_text(raw)
                fresh = VerificationStore(root, control_root=root / 'store')
                with self.assertRaises(StoreError): fresh.admit_repository_verification()

    def test_symlinked_authority_directory_and_artifact_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            outside = root.parent / (root.name + '-outside')
            outside.mkdir()
            self.addCleanup(lambda: __import__('shutil').rmtree(outside, ignore_errors=True))
            store = VerificationStore(root, control_root=root / 'store')
            store.root.mkdir(parents=True)
            store.executions.symlink_to(outside, target_is_directory=True)
            fresh = VerificationStore(root, control_root=root / 'store')
            with self.assertRaises(StoreError): fresh.admit_repository_verification()

    def test_two_process_admissions_cannot_both_reserve_clean_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
            (repo / 'seed').write_text('seed')
            subprocess.run(['git', '-C', str(repo), 'add', 'seed'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'seed'], check=True)
            store, _ = resolve_control_root(repo)
            package = pathlib.Path(__file__).resolve().parents[1]
            script = "import pathlib,sys; sys.path.insert(0,sys.argv[1]); from agent_harness.verification.store import VerificationStore; r=pathlib.Path(sys.argv[2]); s=VerificationStore(r); e=sys.argv[3]; rec={'schema_version':2,'execution_id':e,'repository_id':s.repository_id,'backend':'test','worktree':str(r),'family_id':'f','attempt_id':e,'gate_id':'g','launch_intent_hash':'a'*64,'started_at':1.0};\ntry: s.admit_and_reserve(e,rec); print('ADMITTED')\nexcept Exception as x: print(type(x).__name__+':'+str(x))\n"
            barrier = root / 'go'
            def launch(execution_id):
                return subprocess.Popen([sys.executable, '-c', script, str(package), str(repo), execution_id],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, 'ADMISSION_BARRIER': str(barrier)})
            processes = [launch('exec-one'), launch('exec-two')]
            outputs = [p.communicate(timeout=10) for p in processes]
            text = [out.decode() for stdout, _ in outputs for out in [stdout]]
            admitted = sum('ADMITTED' in out for out in text)
            self.assertEqual(1, admitted, text)
            fresh = VerificationStore(repo)
            self.assertEqual(1, len(fresh._scan_executions()))

    def test_reconciliation_closure_publication_failure_stays_unresolved_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            journal = store.executions / 'exec-close'
            journal.mkdir(parents=True)
            (journal / 'started.json').write_text(json.dumps({
                'schema_version': 2, 'execution_id': 'exec-close', 'repository_id': store.repository_id,
                'backend': 'test', 'worktree': str(root), 'family_id': 'f', 'attempt_id': 'a',
                'gate_id': 'g', 'launch_intent_hash': 'a' * 64, 'started_at': 1.0}))
            def fail(boundary):
                if boundary == 'before-directory-fsync': raise OSError('closure durability fault')
            store._fault_injector = fail
            with self.assertRaises(StoreError):
                store.admit_repository_verification(lambda _: ReconciliationOutcome.PROVEN_DRAINED)
            fresh = VerificationStore(root, control_root=root / 'store')
            with self.assertRaises(StoreError): fresh.admit_repository_verification()

    def test_terminal_corruption_and_projection_contradiction_handling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            terminal = {'schema_version': 2, 'evidence_id': 'eid', 'family_id': 'fam',
                'ownership_token': 'owner', 'gate_id': 'gate', 'repository_id': store.repository_id,
                'profile_hash': 'a' * 64, 'policy_checkpoint': 'b' * 40, 'command_hash': 'c' * 64,
                'candidate_identity': 'e' * 64, 'final_changed_surface_id': 'f' * 64,
                'origin_policy': 'integration', 'pre_fingerprint': 'd' * 64, 'post_fingerprint': 'd' * 64,
                'sandbox': 'off', 'retry_policy': 'forbid', 'process_invocations': 1, 'exit_code': 0,
                'started_at': 1.0, 'ended_at': 2.0, 'status': 'pass', 'artifacts': [], 'dependencies': []}
            from agent_harness.verification.serialization import digest
            terminal['receipt_hash'] = digest(terminal)
            path = store.runs / 'fam' / 'eid' / 'terminal.json'
            publish_create_once(path, terminal)
            projection = path.parent / 'projection.json'
            projection.write_text(json.dumps({'status': 'verification-failed', 'evidence_id': 'forged'}))
            fresh = VerificationStore(root, control_root=root / 'store')
            recovered = fresh.reconstruct_terminals()
            self.assertEqual('pass', recovered[0]['status'])
            self.assertEqual('pass', json.loads(projection.read_text())['status'])
            path.write_text('{')
            with self.assertRaisesRegex(StoreError, 'invalid-terminal-evidence'):
                VerificationStore(root, control_root=root / 'store').reconstruct_terminals()

    def test_projection_only_record_fails_closed_without_synthesizing_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'store')
            projection = store.runs / 'family' / 'evidence' / 'projection.json'
            projection.parent.mkdir(parents=True)
            projection.write_text(json.dumps({'status': 'pass'}))
            with self.assertRaisesRegex(StoreError, 'projection-without-terminal'):
                VerificationStore(root, control_root=root / 'store').reconstruct_terminals()
            self.assertFalse(projection.with_name('terminal.json').exists())


if __name__ == '__main__':
    unittest.main()
