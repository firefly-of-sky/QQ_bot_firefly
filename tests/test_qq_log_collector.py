import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

from scripts.cloud.collect_qq import (advance_observation, bounded_run, collect, prune)
from scripts.cloud.ops import Operations


class LogCollectorTests(unittest.TestCase):
    def online(self):
        return {'application': {'ready': True, 'live': True,
                               'qq_connected': True, 'qq_online': True, 'reason': 'ready'}}

    def test_private_incremental_logs_and_safe_summary_without_docker_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp)
            operation.status=self.online
            calls=[]
            def run(args):
                calls.append(args)
                self.assertIn('logs', args)
                self.assertNotIn('restart', args)
                return NS(stdout='PRIVATE_FIXTURE_CHAT', returncode=0, timed_out=False,
                          truncated=False, bytes_seen=20)
            first=collect(operation, now=1000, log_run=run)
            second=collect(operation, now=1060, log_run=run)
            self.assertEqual(second['logs_until'],1060)
            self.assertNotIn('PRIVATE_FIXTURE_CHAT',json.dumps(second))
            self.assertIn('1970-01-01T00:16:40+00:00',calls[2])
            folder=Path(tmp)/'run/qq-logs';self.assertEqual(folder.stat().st_mode&0o777,0o700)
            path=folder/first['last_sample'];self.assertEqual(path.stat().st_mode&0o777,0o600)
            with gzip.open(path,'rt') as f:row=json.load(f)
            self.assertEqual(row['logs']['napcat']['text'],'PRIVATE_FIXTURE_CHAT')
            self.assertEqual((Path(tmp)/'run/qq-log-collector.json').stat().st_mode&0o777,0o600)

    def test_failed_logs_do_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp);operation.status=self.online
            def run(args):return NS(stdout='',returncode=1,timed_out=False,truncated=False,bytes_seen=0)
            result=collect(operation,now=1000,log_run=run)
            self.assertNotIn('logs_until',result)
            self.assertFalse(result['log_collection_ok'])

    def test_bounded_process_drains_large_output_and_timeout_is_not_success(self):
        r=bounded_run([sys.executable,'-c','print("x"*100000)'],limit=1024)
        self.assertEqual(r.returncode,0);self.assertTrue(r.truncated)
        self.assertEqual(len(r.stdout.encode()),1024)
        r=bounded_run([sys.executable,'-c','import time; time.sleep(5)'],timeout=0.05)
        self.assertTrue(r.timed_out);self.assertNotEqual(r.returncode,0)

    def test_rotation_caps_age_and_total_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for i in range(4):
                p=folder/f'sample-{i}.json.gz';p.write_bytes(b'x'*10);os.utime(p,(i*10,i*10))
            result=prune(folder,now=40,max_age=35,max_bytes=15)
            self.assertEqual(result,{'files':1,'bytes':10})
            self.assertEqual([p.name for p in folder.iterdir()],['sample-3.json.gz'])

    def test_observation_distinguishes_offline_unavailable_and_healthy_window(self):
        saved={};advance_observation(saved,self.online(),now=1000)
        advance_observation(saved,{},now=1060)
        self.assertEqual(saved['observation']['outcome'],'OBSERVING')
        advance_observation(saved,{},now=1120);advance_observation(saved,{},now=1180)
        self.assertEqual(saved['observation']['outcome'],'PROBE_UNAVAILABLE')
        saved={};advance_observation(saved,self.online(),now=1000)
        advance_observation(saved,{'qq_login':{'available':True,'phase':'waiting_qrcode','online':False}},now=1060)
        self.assertEqual(saved['observation']['outcome'],'QQ_OFFLINE')
        saved={};advance_observation(saved,self.online(),now=1000)
        advance_observation(saved,self.online(),now=22600)
        self.assertEqual(saved['observation']['outcome'],'ONLINE_WINDOW_COMPLETED')

    def test_native_kick_is_retained_even_if_current_status_has_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            operation=Operations(tmp);operation.status=self.online
            def run(args):return NS(stdout='[QQbotDiag/v1] '+json.dumps({
                'event':'qq_kicked_offline','at':'1970-01-01T00:16:40Z'}),
                returncode=0,timed_out=False,truncated=False,bytes_seen=100)
            result=collect(operation,now=1000,log_run=run)
            self.assertEqual(result['latest']['qq'],'online')
            self.assertEqual(result['observation']['outcome'],'NATIVE_KICK_OBSERVED')


if __name__=='__main__':unittest.main()
