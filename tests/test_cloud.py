"""Cloud failure handling and backups, without Docker or production data."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from qqbot.health import RuntimeHealth, probe

spec = importlib.util.spec_from_file_location('cloud_ops', Path(__file__).parents[1]/'scripts/cloud/ops.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_stays_live_during_login_and_readiness_tracks_workers(self):
        app = NS(transport=NS(ws=None, online=False), backend=None,
                 conversations={}, relay=NS(losses=0))
        health = RuntimeHealth(app)
        health.port = 0
        await health.start()
        try:
            code, value = await asyncio.to_thread(probe, 'live', port=health.port)
            self.assertEqual(code, 200)
            self.assertEqual(value['reason'], 'qq_login_required')
            code, _ = await asyncio.to_thread(probe, 'ready', port=health.port)
            self.assertEqual(code, 503)
            app.transport = NS(ws=NS(closed=False), online=True)
            app.backend = NS(service=NS(status=lambda: {'running':True},
                tools=NS(chat=NS(receiver=NS(status=lambda: {'state':'connected'})))))
            self.assertTrue(health.snapshot()['ready'])
            app.conversations['fixture'] = NS(error=RuntimeError('fixture'), task=None, ready=asyncio.Event())
            self.assertEqual(health.snapshot()['reason'], 'conversation_recovering')
            health.pulse = time.monotonic()-31
            self.assertFalse(health.snapshot()['live'])
        finally:
            await health.close()


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_watchdog_requires_repeated_failure_and_obeys_maintenance(self):
        calls=[]
        runner=lambda args: calls.append(args) or NS(stdout='',returncode=0)
        operation=ops.Operations(self.root,run=runner,now=lambda:1000)
        status=dict(maintenance=False,services={'bot':{'state':'running','health':'unhealthy'}},disk_used_percent=20)
        operation.status=lambda:status
        for _ in range(2):self.assertEqual(operation.watchdog()['action'],['none'])
        self.assertEqual(operation.watchdog()['action'],['restart_bot'])
        self.assertEqual(calls[-1][-2:],['restart','bot'])
        for _ in range(6):operation.watchdog()
        self.assertEqual(len(calls),1)
        status['maintenance']=True
        self.assertEqual(operation.watchdog()['action'],'maintenance')
        self.assertEqual(len(calls),1)

    def test_expired_login_never_restarts_a_live_process(self):
        operation=ops.Operations(self.root,run=lambda _:self.fail('Unexpected Docker mutation'))
        operation.status=lambda:dict(maintenance=False,services={
            'bot':{'state':'running','health':'healthy'},'napcat':{'state':'running'}},
            application={'reason':'qq_login_required'},disk_used_percent=20)
        for _ in range(8):
            self.assertEqual(operation.watchdog()['action'],['qq_scan_required'])

    def test_acceptance_job_is_excluded_from_resident_container_probe(self):
        def runner(args):
            if args[:2]==['docker','ps']:
                ids='resident' if 'label=com.docker.compose.oneoff=False' in args else 'resident\nacceptance'
                return NS(stdout=ids)
            self.assertEqual(args[-1],'resident')
            return NS(stdout=json.dumps({'Status':'running'}))
        operation=ops.Operations(self.root,run=runner)
        self.assertEqual(operation.container('bot')['Status'],'running')

    def test_missing_onebot_with_qr_wait_is_reported_without_restart(self):
        operation=ops.Operations(self.root,run=lambda _:self.fail('Unexpected Docker mutation'))
        operation.status=lambda:dict(maintenance=False,services={
            'bot':{'state':'running','health':'healthy'},'napcat':{'state':'running'}},
            application={'reason':'qq_connection_pending'},
            qq_login={'available':True,'phase':'waiting_qrcode','online':False},disk_used_percent=20)
        self.assertEqual(operation.watchdog()['action'],['qq_scan_required'])

    def test_quick_login_uses_only_the_current_authenticated_account(self):
        operation=ops.Operations(self.root)
        api=Mock(side_effect=[{'isLogin':True},{'uin':'456'},None])
        operation.napcat_api=lambda:api
        self.assertEqual(operation.configure_login(),{'quick_login_configured':True})
        api.assert_called_with('/QQLogin/SetQuickLoginQQ',{'uin':'456'})

    def test_quick_login_cannot_bind_an_unconfirmed_account(self):
        operation=ops.Operations(self.root)
        api=Mock(return_value={'isLogin':False})
        operation.napcat_api=lambda:api
        with self.assertRaises(RuntimeError):operation.configure_login()
        api.assert_called_once_with('/QQLogin/CheckLoginStatus')

    def test_backup_restores_committed_wal_data_and_keeps_secrets_private(self):
        (self.root/'data/bot').mkdir(parents=True)
        (self.root/'.env').write_text('FIXTURE_SECRET=local-test-only\n')
        source=sqlite3.connect(self.root/'data/bot/chat.sqlite3')
        self.addCleanup(source.close)
        source.execute('PRAGMA journal_mode=WAL')
        source.execute('CREATE TABLE messages (value TEXT)')
        source.execute('INSERT INTO messages VALUES (?)',('committed fixture',))
        source.commit()
        operation=ops.Operations(self.root)
        result=operation.backup()
        archive=self.root/'backups'/result['backup']
        self.assertEqual(archive.stat().st_mode & 0o777,0o600)
        restored=self.root/'restore'
        with tarfile.open(archive) as stream:
            self.assertFalse(any(x.name.endswith(('-wal','-shm')) for x in stream.getmembers()))
            stream.extractall(restored,filter='data')
        with sqlite3.connect(restored/'data/bot/chat.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT value FROM messages').fetchone()[0],'committed fixture')
        self.assertIn('FIXTURE_SECRET=',(restored/'.env').read_text())
        self.assertFalse(json.loads((restored/'snapshot.json').read_text())['qq_device_session'])
