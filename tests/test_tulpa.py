"""Real Tulpa MCP + bridge integration; only the remote QQ/model are fixtures."""
import asyncio
from contextlib import nullcontext
from dataclasses import replace
import json
import os
import socket
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

from qqbot.config import Config, Model
from qqbot.llm import ModelReply, ToolCall
from qqbot.tulpa.app import Application
from qqbot.tulpa.client import connect
from qqbot.tulpa.settings import load_settings


class ModelFixture:
    def __init__(self):
        self.calls = 0
        self.gate = None

    async def turn(self, model, messages, tools):
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        results = [json.loads(m['content']) for m in messages if m['role']=='tool']
        plan = next((m for m in reversed(results) if m.get('state')=='READY'),None)
        if plan:
            return ModelReply(calls=(ToolCall('send','send_chat_reply',json.dumps(dict(
                plan_id=plan['plan_id'],bubbles=[dict(kind='text',text='fixture answer')]))),))
        return ModelReply(calls=(ToolCall('plan','plan_chat_reply',json.dumps(dict(
            action='reply',expressions=['text'],intent='answer fixture'))),))


class NapCatFixture:
    def __init__(self):
        self.clients = set()
        self.sent = []
        self.history = {}
        self.online = True
        self.mid = 10

    async def ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json(dict(post_type='meta_event',meta_event_type='heartbeat',self_id=456,status={'online':self.online,'good':True}))
        try:
            async for frame in ws:
                call = json.loads(frame.data)
                name, args = call['action'], call['params']
                if name=='get_login_info': data={'user_id':456,'nickname':'robot'}
                elif name=='get_status': data={'online':self.online,'good':self.online}
                elif name=='get_friend_list': data=[{'user_id':u,'nickname':'friend'+str(u),'remark':''} for u in (123,124)]
                elif name=='get_group_list': data=[]
                elif name=='get_msg': data=self.history.get(str(args['message_id']))
                elif name=='send_private_msg':
                    self.sent.append(args)
                    data={'message_id':1000+len(self.sent)}
                else:
                    await ws.send_json(dict(echo=call['echo'],status='failed',retcode=1404,data=None));continue
                await ws.send_json(dict(echo=call['echo'],status='ok',retcode=0,data=data))
        finally:
            self.clients.discard(ws)
        return ws

    async def emit(self, text, uid=123, **extra):
        self.mid += 1
        event = dict(post_type='message',message_type='private',sub_type='friend',self_id=456,
                     user_id=uid,message_id=self.mid,time=int(time.time()),
                     sender={'user_id':uid,'nickname':'friend'},message=[{'type':'text','data':{'text':text}}])|extra
        self.history[str(self.mid)] = event
        for ws in list(self.clients):await ws.send_json(event)
        return self.mid


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


async def until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():await asyncio.sleep(.05)


class TulpaIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_real_framework_routing_planning_delivery_and_control(self):
        # One core per process: upstream's ROOT is deliberately immutable after
        # import, like the deployed container. No monkeypatch of framework tools.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = ModelFixture()
            remote = NapCatFixture()
            server = web.Application();server.router.add_get('/ws',remote.ws)
            runner = web.AppRunner(server);await runner.setup()
            site = web.TCPSite(runner,'127.0.0.1',0);await site.start()
            port = site._server.sockets[0].getsockname()[1]
            # An independently hosted MCP extension proves discovery/execution
            # does not require a new Python Tool definition in the bot.
            from mcp.server.fastmcp import FastMCP
            import uvicorn
            extension = FastMCP('fixture-extension',stateless_http=True)
            @extension.tool()
            def nested_sum(items: list[dict[str,int]]) -> dict:
                return {'total':sum(x['value'] for x in items)}
            extension_port=free_port()
            from starlette.middleware.base import BaseHTTPMiddleware
            extension_app=extension.streamable_http_app()
            async def slow_discovery(request,call_next):
                if request.method=='POST' and (await request.json()).get('method')=='tools/list':
                    await asyncio.sleep(2.2)
                return await call_next(request)
            extension_app.add_middleware(BaseHTTPMiddleware,dispatch=slow_discovery)
            extension_server=uvicorn.Server(uvicorn.Config(extension_app,host='127.0.0.1',port=extension_port,log_level='critical',access_log=False))
            extension_server.capture_signals=nullcontext
            extension_task=asyncio.create_task(extension_server.serve())
            await until(lambda:extension_server.started)
            settings_path=root/'tulpa.toml'
            settings_path.write_text(f'[mcp_servers.fixture]\nurl="http://127.0.0.1:{extension_port}/mcp"\ntools=["nested_sum"]\n')
            cfg = Config(owner=123,token='fixture-token',ws_url=f'ws://127.0.0.1:{port}/ws',
                database=str(root/'chat.sqlite3'),models={'primary':Model('http://unused','fixture','UNUSED')},
                default_model='primary',message_merge_seconds=.2,message_merge_max_seconds=1,
                context_budget_bytes=200000,requests_per_minute=100)
            env = {'TULPA_ROOT':str(root/'tulpa'),'TULPA_CONFIG':str(settings_path), 'TULPA_MCP_PORT':str(free_port()), 'TULPA_ADMIN_PORT':'0'}
            async with aiohttp.ClientSession() as http:
                with patch.dict(os.environ,env):
                    app = Application(cfg,model,http)
                    task = asyncio.create_task(app.run())
                    try:
                        await until(lambda: bool(app.conversations) and next(iter(app.conversations.values())).ready.is_set())
                        first = next(iter(app.conversations.values()))
                        self.assertEqual(type(app.backend.service).__module__,'chatlocal.mcp_server')
                        self.assertIn('send_chat_reply',first.peer.tools)
                        ext, name, schema = first.extensions['fixture__nested_sum']
                        self.assertEqual(schema['function']['parameters']['properties']['items']['type'],'array')
                        nested, _ = await ext.call(name,{'items':[{'value':7},{'value':9}]})
                        self.assertEqual(nested['total'],16)
                        await remote.emit('first question')
                        await until(lambda:len(remote.sent)==1)
                        self.assertEqual(remote.sent[0]['user_id'],123)
                        self.assertEqual(remote.sent[0]['message'][0]['data']['text'],'fixture answer')
                        # Same native message replay is deduplicated by Tulpa.
                        for ws in list(remote.clients):await ws.send_json(remote.history[str(remote.mid)])
                        await asyncio.sleep(.5);self.assertEqual(len(remote.sent),1)
                        # Group and non-friend temporary chats never invoke a model.
                        before = model.calls
                        await remote.emit('ignored',message_type='group',group_id=55)
                        await remote.emit('ignored',sub_type='group')
                        await asyncio.sleep(.3);self.assertEqual(model.calls,before)
                        await remote.emit('second friend',uid=124)
                        await until(lambda:len(remote.sent)==2)
                        self.assertEqual(remote.sent[-1]['user_id'],124)
                        second = next(c for c in app.conversations.values() if c.uid==124)
                        self.assertNotEqual(first.grant['id'],second.grant['id'])
                        # A friend's bearer token cannot read another session.
                        denied, _ = await second.peer.call('get_chat_session',{'session_id':first.sid})
                        self.assertIn('error',denied)
                        # Stop cancels an actual in-flight model and confirms once.
                        model.gate = asyncio.Event()
                        await remote.emit('slow question')
                        await until(lambda:first.turn_task is not None)
                        await remote.emit('不用查了')
                        await until(lambda:len(remote.sent)==3)
                        self.assertIn('停止',remote.sent[-1]['message'][0]['data']['text'])
                        model.gate.set();model.gate=None
                        await asyncio.sleep(.4);self.assertEqual(len(remote.sent),3)
                        # Reset rotates the framework session and purges its inbox.
                        old_sid = first.sid
                        await remote.emit('/reset')
                        await until(lambda:first.sid!=old_sid)
                        self.assertEqual(app.store.history(first.key,20),[])
                        # A plan expires on relevant new input, including a
                        # correction; the framework rejects the old send.
                        await remote.emit('stale original')
                        model.gate=asyncio.Event()
                        await until(lambda:first.turn_task is not None)
                        read,_=await first.peer.call('get_chat_session',{'session_id':first.sid})
                        await asyncio.sleep(.3)
                        plan,_=await first.peer.call('plan_chat_reply',dict(session_id=first.sid,batch_id=read['input_batch']['id'],action='reply',intent='fixture stale gate',expressions=['text']))
                        self.assertEqual(plan['state'],'READY')
                        await remote.emit('我刚才说错了，是新问题')
                        await asyncio.sleep(.3)
                        rejected,_=await first.peer.call('send_chat_reply',dict(session_id=first.sid,plan_id=plan['plan_id'],bubbles=[{'kind':'text','text':'must never arrive'}]))
                        self.assertEqual(rejected['state'],'REJECTED')
                        self.assertNotIn('must never arrive',str(remote.sent))
                        model.gate.set();model.gate=None
                        await until(lambda:first.turn_task is None,timeout=15)
                        # Framework's real auth and nested-schema validation.
                        bad, _ = await first.peer.call('send_chat_reply',dict(session_id=first.sid,plan_id='a'*32,bubbles='invalid'))
                        self.assertEqual(bad.get('error_code'),'invalid_arguments')
                        async with http.post(f'http://127.0.0.1:{app.relay.port}/get_login_info',json={}) as response:
                            self.assertEqual(response.status,401)
                    finally:
                        task.cancel();await asyncio.gather(task,return_exceptions=True)
                        await runner.cleanup()
                        extension_server.should_exit=True
                        await extension_task


class TulpaAuthorization(unittest.IsolatedAsyncioTestCase):
    async def test_friend_bootstrap_failure_keeps_other_sessions_connected(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from qqbot.tulpa.relay import Relay
        relay=Relay(None)
        client=SimpleNamespace()
        # SimpleNamespace isn't hashable; model the actual WebSocket identity.
        class Client:
            send_json=AsyncMock()
            close=AsyncMock()
        client=Client();relay.clients.add(client)
        relay.on_event=AsyncMock(side_effect=RuntimeError('capacity'))
        relay.receive({'post_type':'message','message_id':1})
        worker=asyncio.create_task(relay.publish())
        try:
            await asyncio.wait_for(relay.events.join(),1)
            client.send_json.assert_awaited_once()
            client.close.assert_not_awaited()
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)

    async def test_new_chat_input_interrupts_background_learning(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,Mock
        app=Application.__new__(Application)
        app.account='456';app.friends={'123'};app.last_directory=time.monotonic()
        app.settings={'friends':'all'};app.cfg=SimpleNamespace(owner=123)
        conversation=SimpleNamespace(learning=True,last_event=0,interrupt=Mock())
        app.ensure=AsyncMock(return_value=conversation)
        await app.event(dict(post_type='message',message_type='private',sub_type='friend',
                             self_id=456,user_id=123,message=[{'type':'text','data':{'text':'ordinary new question'}}]))
        conversation.interrupt.assert_called_once()

    async def test_parallel_image_and_query_keep_tool_responses_contiguous(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,Mock
        from mcp.types import ImageContent
        from qqbot.tulpa.runner import Conversation
        cfg=Config(owner=123,token='fixture',ws_url='ws://unused',database=':memory:',
                   models={'primary':Model('http://unused','fixture','UNUSED')},default_model='primary',auto_search=False)
        llm=SimpleNamespace(turn=AsyncMock(side_effect=[
            ModelReply(calls=(ToolCall('image','read_chat_image','{"event_id":1}'),
                              ToolCall('math','calculate','{"expression":"1+1"}'))),
            ModelReply(calls=(ToolCall('done','plan_chat_reply','{"action":"silence","intent":"fixture"}'),))]))
        store=SimpleNamespace(history=lambda *args:[],append=Mock())
        conversation=Conversation(SimpleNamespace(cfg=cfg,llm=llm,store=store),'456:direct:123',{})
        conversation.sid='a'*32;conversation.extensions={}
        calls=[]
        async def call(name,args):
            calls.append((name,args))
            if name=='get_chat_session':return {'context':{'messages':[]},'input_batch':{'id':'c'*32}},[]
            if name=='read_chat_image':return {'ok':True},[ImageContent(type='image',data='ZmFrZQ==',mimeType='image/png')]
            return {'state':'SILENT'},[]
        conversation.peer=SimpleNamespace(call=call,schemas=lambda *args:[])
        packet={'input_batch':{'id':'b'*32},'read_through_id':1}
        items=[{'id':1,'content':'synthetic image and arithmetic','onebot_message_id':'11'}]
        self.assertTrue(await conversation.infer(packet,items,'primary'))
        messages=llm.turn.call_args_list[1].args[1]
        at=next(i for i,m in enumerate(messages) if m.get('tool_calls'))
        self.assertEqual([m['role'] for m in messages[at:at+4]],['assistant','tool','tool','user'])
        self.assertEqual([m['tool_call_id'] for m in messages[at+1:at+3]],['image','math'])
        self.assertEqual(messages[at+3]['content'][1]['type'],'image_url')
        self.assertEqual(next(args['batch_id'] for name,args in calls if name=='plan_chat_reply'),'b'*32)

    async def test_revoked_grant_is_not_automatically_replaced(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from qqbot.tulpa.backend import Backend
        backend=Backend.__new__(Backend)
        backend.credentials={'456:direct:123':{'id':'saved','token':'revoked'}}
        backend.access=SimpleNamespace(authorize=Mock(side_effect=ValueError('revoked')),create=Mock())
        with self.assertRaises(ValueError):
            await backend.grant('456:direct:123')
        backend.access.create.assert_not_called()


class TulpaZImageDNS(unittest.TestCase):
    def test_mixed_translated_fake_dns_resolves_and_private_answers_stay_blocked(self):
        # The integration test imports the real vendored core first. A separate
        # invocation can import it without initializing a store.
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor/tulpa'))
        from chatlocal.mcp_chat_media import public_addresses
        from unittest.mock import MagicMock
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.67',443)),
                 (socket.AF_INET6,socket.SOCK_STREAM,6,'',('::ffff:0:c612:43',443,0,0))]
        response=MagicMock()
        response.__enter__.return_value=response
        response.stream.return_value.__enter__.return_value=response
        for answer,allowed in (('1.1.1.1',True),('127.0.0.1',False),('10.0.0.1',False)):
            response.iter_bytes.return_value=iter([json.dumps({'Answer':[{'type':1,'data':answer}]}).encode()])
            with patch('socket.getaddrinfo',return_value=records),patch('chatlocal.mcp_chat_media.httpx.Client',return_value=response):
                if allowed:self.assertEqual(public_addresses('multimedia.nt.qq.com.cn',443)[0][4][0],answer)
                else:
                    with self.assertRaises(ValueError):public_addresses('multimedia.nt.qq.com.cn',443)


class TulpaSettings(unittest.TestCase):
    def test_reject_insecure_extension_and_excess_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'tulpa.toml'
            for text in ('[conversations]\nmax_sessions=9','[mcp_servers.bad]\nurl="http://external.example/mcp"'):
                path.write_text(text)
                with patch.dict(os.environ,{'TULPA_CONFIG':str(path)}):
                    with self.assertRaises(ValueError):load_settings()
