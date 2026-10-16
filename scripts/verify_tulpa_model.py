"""Opt-in billable Tulpa acceptance: real model + real MCP, synthetic QQ only."""
import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import ssl
import certifi

import aiohttp
from aiohttp import web

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_tulpa import NapCatFixture, until, free_port
from qqbot.__main__ import load_env_file
from qqbot.config import load
from qqbot.llm import LLM
from qqbot.tulpa.app import Application


class TrackedLLM(LLM):
    def __init__(self,*args):
        super().__init__(*args)
        self.used=[]
    async def turn(self,*args,**kwargs):
        reply=await super().turn(*args,**kwargs)
        self.used.extend(c.name for c in reply.calls)
        return reply


async def main():
    load_env_file('.env')
    original = load()
    remote = NapCatFixture()
    server=web.Application();server.router.add_get('/ws',remote.ws)
    runner=web.AppRunner(server);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    reports=[]
    with tempfile.TemporaryDirectory(prefix='qqbot-tulpa-model-') as tmp:
        os.environ['TULPA_MCP_PORT']=str(free_port())
        os.environ['TULPA_ADMIN_PORT']='0'
        os.environ['TULPA_ROOT']=str(Path(tmp)/'tulpa')
        os.environ['TULPA_CONFIG']=str(Path(tmp)/'absent.toml')
        cfg=replace(original,owner=123,token='fixture',database=str(Path(tmp)/'chat.sqlite3'),
                    ws_url=f'ws://127.0.0.1:{port}/ws',auto_search=False,
                    message_merge_seconds=.3,message_merge_max_seconds=1,requests_per_minute=100)
        async with aiohttp.ClientSession(trust_env=True,connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))) as http:
            llm=TrackedLLM(http,cfg)
            app=Application(cfg,llm,http)
            task=asyncio.create_task(app.run())
            try:
                def ready():
                    if task.done():raise task.exception() or RuntimeError('runtime stopped')
                    return bool(app.conversations) and next(iter(app.conversations.values())).ready.is_set()
                await until(ready)
                cases=[('arithmetic',['E2E30模型：必须调用 calculate 计算 17*29，只回复计算结果。'],'493'),
                       ('burst',['E2E30合并：把后面两条用斜杠连接，只回复结果。','青羽','615'],'青羽/615'),
                       ('correction',['E2E30更正：请只回复旧数值 111。','我刚才说错了，请只回复新数值 827。'],'827')]
                for name,messages,expected in cases:
                    before=len(remote.sent)
                    tool_start=len(llm.used)
                    for text in messages:
                        await remote.emit(text);await asyncio.sleep(.08)
                    await until(lambda:len(remote.sent)>before,timeout=180)
                    await asyncio.sleep(1)
                    answers=[''.join(s.get('data',{}).get('text','') for s in x['message']) for x in remote.sent[before:]]
                    used=llm.used[tool_start:]
                    passed=len(answers)==1 and expected in answers[0] and 'plan_chat_reply' in used and 'send_chat_reply' in used
                    if name=='arithmetic':passed=passed and 'calculate' in used
                    reports.append({'test':name,'passed':passed,'reply_count':len(answers),'reply':answers[0] if answers else '', 'tools':used})
                    print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
                    assert passed,reports[-1]
                assert all(r['passed'] for r in reports),reports
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                await runner.cleanup()


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    asyncio.run(main())
