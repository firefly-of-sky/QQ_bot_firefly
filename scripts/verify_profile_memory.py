"""Real Tulpa MCP profile/new-window/forget acceptance; synthetic QQ, optional real model."""
import argparse,asyncio,json,os,sys,tempfile
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import aiohttp
import ssl,certifi
from aiohttp import web
from test_tulpa import NapCatFixture,ModelFixture,until,free_port
from qqbot.config import Config,Model,load
from qqbot.llm import LLM,ModelReply
from qqbot.__main__ import load_env_file
from qqbot.tulpa.app import Application

class ProfileModel(ModelFixture):
 async def turn(self,model,messages,tools):
  if not tools:return ModelReply(text=json.dumps({'updates':[dict(key='回答风格',value='先给结论再简短解释',kind='preference',confidence='explicit',evidence='我平时最喜欢先给结论再简短解释')]}))
  return await super().turn(model,messages,tools)

async def main(real=False):
 if real and Path('.env').exists():load_env_file('.env')
 with tempfile.TemporaryDirectory(prefix='qqbot-profile-') as tmp:
  root=Path(tmp);remote=NapCatFixture();server=web.Application();server.router.add_get('/ws',remote.ws);runner=web.AppRunner(server);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
  os.environ.update(TULPA_ROOT=str(root/'tulpa'),TULPA_CONFIG=str(root/'no-config'),TULPA_MCP_PORT=str(free_port()),TULPA_ADMIN_PORT='0',QQBOT_HEALTH_PORT='0')
  cfg=replace(load() if real else Config(owner=123,token='fixture',ws_url='',database='',models={'primary':Model('http://unused','fixture','UNUSED')},default_model='primary'),owner=123,token='fixture',ws_url=f'ws://127.0.0.1:{port}/ws',database=str(root/'chat.db'),auto_search=False,message_merge_seconds=.1,message_merge_max_seconds=1,requests_per_minute=100)
  seen=[]
  async with aiohttp.ClientSession(trust_env=True,connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))) as http:
   base=LLM(http,cfg) if real else ProfileModel()
   class Tracked:
    async def turn(self,model,messages,tools):
     if tools:
      for msg in messages:
       if msg['role']=='user' and isinstance(msg['content'],str):
        try:
         d=json.loads(msg['content'])
         if 'new_messages' in d:seen.append(d)
        except (ValueError,TypeError):pass
     return await base.turn(model,messages,tools)
   app=Application(cfg,Tracked(),http);task=asyncio.create_task(app.run())
   try:
    await until(lambda:bool(app.conversations) and next(iter(app.conversations.values())).ready.is_set())
    c=next(iter(app.conversations.values()))
    await remote.emit('我平时最喜欢先给结论再简短解释，请把这当作长期沟通偏好。')
    await until(lambda:bool(app.store.profile(c.key)),timeout=90)
    assert any(r['kind']=='preference' for r in app.store.profile(c.key))
    old=c.sid;cid=c.cid;await remote.emit('/new');await until(lambda:app.conversations[cid].sid not in (None,old),timeout=30);c=app.conversations[cid]
    assert app.store.history(c.key,20)==[] and app.store.profile(c.key)
    seen.clear();before=len(remote.sent);await remote.emit('按我平时的习惯解释一下什么是光合作用。');await until(lambda:len(remote.sent)>before,timeout=90)
    assert any(p.get('user_profile') for p in seen)
    assert not app.store.profile('private:456:124')
    await until(lambda:c.turn_task is None,timeout=30)
    old=c.sid;await remote.emit('/forget');await until(lambda:app.conversations[cid].sid not in (None,old),timeout=30);c=app.conversations[cid]
    await app.profiles.flush(c.key);assert not app.store.profile(c.key)
    print(json.dumps({'real_model':real,'profile_distilled':True,'new_window_rotated':True,'short_context_cleared':True,'profile_used_after_new_window':True,'other_friend_isolated':True,'forget_cleared_profile':True,'real_qq_messages_sent':False}))
   finally:
    task.cancel();await asyncio.gather(task,return_exceptions=True);await runner.cleanup()
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--real-model',action='store_true');args=parser.parse_args();asyncio.run(main(args.real_model))
