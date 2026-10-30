"""Opt-in billable A/B: real model + Tulpa, synthetic QQ and isolated state.

Run each variant in a fresh process; upstream's ROOT is intentionally immutable.
No production chat, configuration, login, or service lifecycle is changed.
"""
import argparse
import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
import uuid

import aiohttp
from aiohttp import web
import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
from test_tulpa import ModelFixture, NapCatFixture, free_port, until
from qqbot.__main__ import load_env_file
from qqbot.config import load
from qqbot.llm import LLM
from qqbot.tulpa.app import Application
from qqbot.tulpa.backend import Backend
from qqbot.tulpa.client import connect
from qqbot.tulpa.runner import Conversation

VARIANTS = {'baseline': (False, False), 'context': (True, False),
            'bubble': (False, True), 'both': (True, True)}


class Sent(list):
    def __init__(self):
        super().__init__()
        self.times=[]

    def append(self, value):
        self.times.append(time.monotonic())
        super().append(value)


class MeasuredLLM(LLM):
    def __init__(self, *args):
        super().__init__(*args)
        self.measurements=[]

    async def turn(self, model, conversation, tools=None):
        size=len(json.dumps(conversation, ensure_ascii=False).encode())
        started=time.monotonic()
        reply=await super().turn(model, conversation, tools)
        self.measurements.append(dict(seconds=round(time.monotonic()-started,3),
            prompt_bytes=size, tools=[c.name for c in reply.calls]))
        return reply


def seed(app, conv):
    """Synthetic twelve-turn archive: old memory + exact recent duplicates."""
    chat=app.backend.service.tools.chat
    app.store.reset(conv.key)
    for i in range(12):
        if i==0:
            user='请记住：出行预算上限1800元，不吃花生，只能周末。我的口令是海棠642。'
            answer='记住了：预算1800元以内、避开花生、仅周末，口令海棠642。'
        elif i==11:
            user='待比较的数据：甲是12，乙是19。'
            answer='已记录：甲12，乙19。'
        else:
            user=f'背景资料第{i}条：我们在准备一次周末短途旅行，先保留资料，稍后再问。'
            answer=f'第{i}条资料已记录，等待具体问题。'
        mid=2000+i*2
        for own, text in [(False,user),(True,answer)]:
            chat.receive(dict(post_type='message_sent' if own else 'message',message_type='private',sub_type='friend',
                self_id=456,user_id=456 if own else 123,target_id=123,message_id=mid+int(own),
                time=time.time(),sender={'user_id':456 if own else 123,'nickname':'fixture'},
                message=[{'type':'text','data':{'text':text}}]))
        app.store.append(conv.key,user,answer,20,[str(mid)])


CASES = [
    ('memory', ['按照之前的出行约束给一个简短建议，明确写出预算上限、饮食限制和出行时间，最后写出口令。']),
    ('correction', ['请只回复数值111。','我刚才说错了，请只回复更正后的数值827。']),
    ('reference', ['引用消息中的甲乙两项相差多少？必须调用calculate，只回复数值。']),
    ('short_points', ['说明两点：管理页只允许本机访问；更新之前先备份。用简短中文，不要省略任一点。']),
    ('explicit_split', ['请分两条消息发送，第一条只发“松”，第二条只发“岚”，不要加其他内容。']),
]

REPLAY_TEXTS = [
    '管理页只允许本机访问，不要把6099端口开放到公网。若需管理，请通过专用SSH隧道访问本机地址，操作结束后关闭隧道即可，机器人仍会继续运行。',
    '更新之前先备份，确认备份完成后再操作。',
]


class ReplayLLM(ModelFixture):
    """Fixed tool arguments isolate Tulpa's bubble delay from API variance."""
    def __init__(self):
        super().__init__()
        self.measurements=[]
        self.bubbles=[{'kind':'text','text':text} for text in REPLAY_TEXTS]

    async def turn(self, model, conversation, tools=None):
        reply=await super().turn(model,conversation,tools)
        self.measurements.append(dict(seconds=0,prompt_bytes=len(json.dumps(conversation,ensure_ascii=False).encode()),
                                      tools=[c.name for c in reply.calls]))
        return reply


def check(name, answers, used):
    text='\n'.join(answers)
    if name=='short_replay':return text=='\n'.join(REPLAY_TEXTS)
    if name=='memory':
        # Merely mentioning the constraints is insufficient: reject an actual
        # weekday departure/return recommendation that contradicts the seed.
        weekday_trip=re.search(r'(?:周|星期)[一二三四五][^，。；\n]{0,10}(?:出发|启程|返程|返回|出行)',text)
        weekend=any(term in text for term in ['周末','周六','周日','星期六','星期日'])
        return all(term in text for term in ['1800','花生','海棠642']) and weekend and not weekday_trip
    if name=='correction':return '827' in text and '111' not in text
    if name=='reference':return text.strip()=='7' and 'calculate' in used
    if name=='short_points':return ('本机' in text or '127.0.0.1' in text or 'localhost' in text) and '备份' in text and '更新' in text
    if name=='explicit_split':return [a.strip() for a in answers]==['松','岚']
    return False


async def benchmark(args):
    if Path('.env').is_file():load_env_file('.env')
    original=load()
    remote=NapCatFixture();remote.sent=Sent()
    server=web.Application();server.router.add_get('/ws',remote.ws)
    runner=web.AppRunner(server);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    results=[]
    with tempfile.TemporaryDirectory(prefix='qqbot-latency-ab-') as tmp:
        os.environ.update(TULPA_ROOT=str(Path(tmp)/'tulpa'),TULPA_MCP_PORT=str(free_port()),
            TULPA_ADMIN_PORT='0',QQBOT_HEALTH_PORT='0',TULPA_CONFIG=str(Path(tmp)/'absent.toml'))
        cfg=replace(original,owner=123,token='fixture',database=str(Path(tmp)/'chat.sqlite3'),
            ws_url=f'ws://127.0.0.1:{port}/ws',auto_search=False,requests_per_minute=100)
        async with aiohttp.ClientSession(trust_env=True,
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))) as http:
            llm=ReplayLLM() if args.replay_only else MeasuredLLM(http,cfg)
            app=Application(cfg,llm,http)
            context,bubble=VARIANTS[args.variant]
            app.settings['performance']=dict(compact_context=context,single_bubble_short_replies=bubble)
            await app.relay.start()
            workers=[asyncio.create_task(app.transport.run(lambda _:False,on_event=app.relay.receive)),
                     asyncio.create_task(app.relay.publish())]
            try:
                await until(lambda:app.transport.ws is not None)
                await app.directory()
                app.backend=Backend(Path(tmp)/'tulpa',app.relay,port=int(os.environ['TULPA_MCP_PORT']))
                policy=dict(quiet_seconds=max(.1,cfg.message_merge_seconds),max_collect_seconds=cfg.message_merge_max_seconds)
                (app.backend.root/'data/mcp-chat-policy.json').write_text(json.dumps(policy))
                await app.backend.start()
                grant=await app.backend.grant('456:direct:123')
                async with connect(app.backend.url,grant['token']) as peer:
                    await peer.discover()
                    cases=[('short_replay',['给我简短的管理和更新建议。'])] if args.replay_only else CASES
                    for name,inputs in cases:
                        conv=Conversation(app,'456:direct:123',grant);conv.peer=peer;conv.extensions={}
                        packet,_=await peer.call('start_chat_session',dict(conversation_id=conv.cid,
                            persona=conv.persona(),participation='natural',idempotency_key=uuid.uuid4().hex))
                        conv.sid=packet['session']['id']
                        await until(lambda:app.backend.service.tools.chat.receiver.status()['state']=='connected')
                        seed(app,conv)
                        drained,_=await peer.call('wait_chat_messages',dict(session_id=conv.sid,quiet_seconds=.1,timeout_seconds=2))
                        if len(drained.get('messages',[]))!=24:raise RuntimeError('Incomplete synthetic seed')
                        before=len(remote.sent);first_call=len(llm.measurements);started=time.monotonic()
                        error=None
                        try:
                            for text in inputs:
                                extra={}
                                if name=='reference':
                                    extra['message']=[{'type':'reply','data':{'id':'2022'}}, {'type':'text','data':{'text':text}}]
                                await remote.emit(text,**extra)
                                if len(inputs)>1:await asyncio.sleep(.08)
                            packet,_=await peer.call('wait_chat_messages',dict(session_id=conv.sid,
                                acknowledge_through_id=drained['read_through_id'],timeout_seconds=10))
                            items=[m for m in packet['messages'] if not m.get('is_self')]
                            async with asyncio.timeout(cfg.agent_timeout_seconds):
                                await conv.infer(packet,items,cfg.default_model)
                        except Exception as exc:error=type(exc).__name__
                        finished=time.monotonic()
                        measurements=llm.measurements[first_call:]
                        used=[tool for r in measurements for tool in r['tools']]
                        answers=[''.join(s.get('data',{}).get('text','') for s in x['message']) for x in remote.sent[before:]]
                        result=dict(variant=args.variant,repetition=args.repetition,case=name,real_model=not args.replay_only,
                            passed=not error and check(name,answers,used),error=error,
                            first_reply_s=round(remote.sent.times[before]-started,3) if answers else None,
                            complete_s=round(finished-started,3),reply_count=len(answers),
                            model_calls=len(measurements),model_seconds=round(sum(r['seconds'] for r in measurements),3),
                            first_prompt_bytes=measurements[0]['prompt_bytes'] if measurements else None,
                            tools=used,answers=answers)
                        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
                        await peer.call('stop_chat_session',{'session_id':conv.sid})
            finally:
                for task in workers:task.cancel()
                await asyncio.gather(*workers,return_exceptions=True)
                if app.backend:await app.backend.close()
                await app.relay.close();app.store.close();await runner.cleanup()
    if args.output:
        Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    return all(r['passed'] for r in results)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-billable',action='store_true',help='Consent to real model requests; synthetic QQ only')
    parser.add_argument('--variant',choices=VARIANTS,required=True)
    parser.add_argument('--repetition',type=int,default=1)
    parser.add_argument('--output')
    parser.add_argument('--replay-only',action='store_true',help='No model request: fixed bubbles through real Tulpa')
    args=parser.parse_args()
    if not args.allow_billable and not args.replay_only:parser.error('Pass --allow-billable for real API usage')
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(0 if asyncio.run(benchmark(args)) else 1)
