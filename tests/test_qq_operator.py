import asyncio
from pathlib import Path
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,patch

from qqbot.onebot import DeliveryError
from qqbot.tulpa.operator import QQOperator
from qqbot.tulpa.operator_store import OperatorStore
from qqbot.tulpa.settings import load_settings
from scripts.cloud.capabilities import inspect_bundle


class LedgerTests(unittest.TestCase):
    def test_durable_idempotency_uncertainty_latch_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'operator.sqlite3';store=OperatorStore(path)
            key,cached=store.begin('turn','qq_qzone_action','send_qzone_msg',{'content':'one'},10)
            self.assertIsNone(cached)
            _,cached=store.begin('turn','qq_qzone_action','send_qzone_msg',{'content':'changed'},10)
            self.assertEqual(cached['state'],'UNKNOWN')
            restarted=OperatorStore(path)
            _,cached=restarted.begin('turn','qq_qzone_action','send_qzone_msg',{'content':'one'},10)
            self.assertEqual(cached['state'],'UNKNOWN')
            restarted.finish(key,{'state':'SUCCEEDED','receipt':'actual'})
            _,cached=restarted.begin('turn','qq_qzone_action','send_qzone_msg',{'content':'one'},10)
            self.assertEqual(cached['state'],'SUCCEEDED')
            self.assertTrue(cached['cached'])
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_rate_limit_pause_and_background_dedup_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'operator.sqlite3';store=OperatorStore(path)
            key,_=store.begin('a','tool','act',{},1);store.finish(key,{'state':'SUCCEEDED'})
            _,limited=store.begin('b','tool','act',{},1)
            self.assertEqual(limited['state'],'RATE_LIMITED')
            self.assertTrue(store.enqueue('event','125'))
            self.assertFalse(store.enqueue('event','125'))
            store.pause(True)
            self.assertIsNone(store.next_job(12))
            store.pause(False)
            self.assertEqual(store.next_job(12)['subject'],'125')
            recovered=OperatorStore(path)
            self.assertEqual(recovered.summary()['jobs'],{'UNKNOWN':1})
            self.assertIsNone(recovered.next_job(12))


class ConfigurationTests(unittest.TestCase):
    def test_explicit_host_authorization_and_strict_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tulpa.toml'
            with patch.dict('os.environ',{'TULPA_CONFIG':str(p)}):
                self.assertFalse(load_settings()['autonomy']['enabled'])
                for text in ['enabled=true','enabled="yes"','enabled=false\nwrites_per_minute=0',
                             'enabled=false\nbackground_friend_requests=1','enabled=false\nunknown=true']:
                    p.write_text('[autonomy]\n'+text)
                    with self.assertRaises(ValueError):load_settings()
                p.write_text('[autonomy]\nenabled=true\nauthorization_id="explicit-broad-v5"\n')
                self.assertTrue(load_settings()['autonomy']['enabled'])

    def test_registered_action_parser_does_not_execute_or_trust_comments(self):
        lines=['const fake = {','  Example: "not_an_action"','};','const actions = {',
               '  SendPrivateMsg: "send_private_msg",','  Status: "get_status",','  Login: "get_login_info",',
               '  // Security: "get_credentials",']
        lines += [f'  Action{i}: "fixture_{i}",' for i in range(45)]
        text='\n'.join(lines+['};','const another = { A: "other" };'])
        result=inspect_bundle(text)
        self.assertEqual(len(result['api_actions']),48)
        self.assertNotIn('not_an_action',result['api_actions'])
        self.assertNotIn('get_credentials',result['api_actions'])
        with self.assertRaises(ValueError):inspect_bundle('not a program registry')


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_dispatch_is_not_replayed_with_new_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            operator=QQOperator.__new__(QQOperator)
            operator.app=NS(account='456',cfg=NS(owner=123))
            operator.policy={'writes_per_minute':10}
            operator.store=OperatorStore(Path(tmp)/'calls.sqlite3')
            schema={'type':'object','properties':{'action':{'const':'set_friend_remark'},
                'params':{'type':'object'},'idempotency_key':{'type':'string'}},
                'required':['action','params','idempotency_key'],'additionalProperties':False}
            operator.catalog=AsyncMock(return_value={'qq_friend_action':dict(schema=schema,
                actions={'set_friend_remark':dict(write=True,available=True)})})
            operator.authorize=lambda:None
            operator.peer=NS(call=AsyncMock(side_effect=TimeoutError))
            actor=NS(cid='456:direct:123')
            args=dict(tool='qq_friend_action',action='set_friend_remark',params={'user_id':'124','remark':'one'})
            with self.assertRaises(TimeoutError):
                await operator.execute(args,turn='turn',conversation=actor)
            args['params']['remark']='changed'
            result,_=await operator.execute(args,turn='turn',conversation=actor)
            self.assertEqual(result['state'],'UNKNOWN')
            operator.peer.call.assert_awaited_once()

    async def test_missing_post_receipt_is_unknown_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            operator=QQOperator.__new__(QQOperator)
            operator.app=NS(account='456',cfg=NS(owner=123))
            operator.policy={'writes_per_minute':10}
            operator.store=OperatorStore(Path(tmp)/'calls.sqlite3')
            operator.catalog=AsyncMock(return_value={'qq_qzone_action':dict(
                schema={'type':'object'},
                actions={'send_qzone_msg':dict(write=True,available=True)})})
            operator.authorize=lambda:None
            operator.peer=NS(call=AsyncMock(return_value=({'state':'SUCCEEDED','result':{'data':None}},[])))
            result,_=await operator.execute(dict(tool='qq_qzone_action',action='send_qzone_msg',params={}),
                                            turn='turn',conversation=NS(cid='456:direct:123'))
            self.assertEqual(result['state'],'UNKNOWN')


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_definite_rejection_and_uncertain_transport_are_distinct(self):
        from aiohttp import web
        from qqbot.tulpa.relay import Relay
        request=NS(json=AsyncMock(return_value={}),match_info={'action':'send_qzone_msg'})
        relay=Relay(NS(action=AsyncMock(side_effect=DeliveryError('safe',retcode=1404,rejected=True))))
        response=await relay.action(request)
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(response.text)['retcode'],1404)
        relay.transport.action.side_effect=DeliveryError('network uncertainty')
        with self.assertRaises(web.HTTPServiceUnavailable):await relay.action(request)

    async def test_napcat_folder_alias_preserves_payload(self):
        from qqbot.tulpa.relay import Relay
        transport=NS(action=AsyncMock(return_value={}))
        relay=Relay(transport)
        request=NS(json=AsyncMock(return_value={'group_id':222,'folder_id':'folder'}),
                   match_info={'action':'delete_group_file_folder'})
        await relay.action(request)
        transport.action.assert_awaited_once_with('delete_group_folder',{'group_id':222,'folder_id':'folder'})


class OperatorIntegrationTests(unittest.TestCase):
    def test_real_mcp_qq_operator_in_its_own_framework_process(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,str(root/'scripts/verify_qq_operator.py')],
            cwd=root,capture_output=True,text=True,timeout=100)
        self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-4000:])
        rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertTrue(rows[-1]['passed'])
        self.assertTrue(rows[-1]['synthetic_only'])
