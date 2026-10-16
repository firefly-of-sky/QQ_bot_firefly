"""QQ channel host: route authorized friends to isolated Tulpa MCP agents."""
import asyncio
import json
import logging
import os
from pathlib import Path
import time

from ..onebot import OneBot, DeliveryError
from ..store import Store
from ..health import RuntimeHealth
from ..turns import control, plain_text
from .backend import Backend
from .relay import Relay
from .runner import Conversation
from .settings import load_settings

log = logging.getLogger(__name__)


class Application:
    def __init__(self, cfg, llm, http):
        self.cfg, self.llm = cfg, llm
        self.settings = load_settings()
        self.transport = OneBot(http,cfg.ws_url,cfg.token)
        self.relay = Relay(self.transport)
        self.store = Store(cfg.database)
        self.slots = asyncio.Semaphore(cfg.max_concurrent_requests)
        self.conversations = {}
        self.rotate = set()
        self.friends = set()
        self.account = None
        self.last_directory = 0
        self.backend = None
        self.tasks = []
        self.health = RuntimeHealth(self)
        self.recovery_at = {}

    async def directory(self):
        login = await self.transport.action('get_login_info',{})
        status = await self.transport.action('get_status',{})
        if status.get('online') is not True:
            raise DeliveryError('QQ offline')
        account = str(login['user_id'])
        if self.account and self.account!=account:
            for c in list(self.conversations.values()):
                await self.remove(c)
        self.account = account
        friends = await self.transport.action('get_friend_list',{})
        self.friends = {str(row['user_id']) for row in friends if isinstance(row,dict) and str(row.get('user_id','')).isdigit()}
        self.last_directory = time.monotonic()

    async def ensure(self, uid):
        cid = f'{self.account}:direct:{uid}'
        old = self.conversations.get(cid)
        if old and not old.task.done():
            return old
        if old is None and len(self.conversations)>=self.settings['max_sessions']:
            idle = sorted((c for c in self.conversations.values() if not c.turn_task),key=lambda c:c.last_event)
            if not idle:
                raise RuntimeError('All Tulpa chat slots are busy')
            await self.remove(idle[0])
        grant = await self.backend.grant(cid)
        conversation = Conversation(self,cid,grant)
        self.conversations[cid] = conversation
        conversation.task = asyncio.create_task(conversation.run())
        try:
            await asyncio.wait_for(conversation.ready.wait(),15)
            if conversation.error:
                raise RuntimeError('Tulpa session unavailable')
        except BaseException:
            conversation.task.cancel()
            await asyncio.gather(conversation.task,return_exceptions=True)
            if old:self.conversations[cid] = old
            else:self.conversations.pop(cid,None)
            raise
        return conversation

    async def remove(self, conversation):
        # Stop through the real MCP endpoint first, invalidating queued sends.
        if conversation.sid and not conversation.task.done():
            try:
                await conversation.peer.call('stop_chat_session',{'session_id':conversation.sid})
            except Exception:
                pass
        conversation.task.cancel()
        await asyncio.gather(conversation.task,return_exceptions=True)
        self.conversations.pop(conversation.cid,None)

    async def event(self, event):
        if event.get('post_type')=='notice' and event.get('notice_type')=='friend_recall':
            cid = f"{event.get('self_id')}:direct:{event.get('user_id')}"
            conversation = self.conversations.get(cid)
            if conversation:
                conversation.interrupt()
                self.store.recall(conversation.key,event.get('message_id'))
            return
        if event.get('post_type')!='message' or event.get('message_type')!='private' or event.get('sub_type')!='friend':
            return
        if event.get('group_id') or str(event.get('user_id'))==str(event.get('self_id')):
            return
        if time.monotonic()-self.last_directory>60 or str(event.get('user_id')) not in self.friends:
            await self.directory()
        uid = str(event.get('user_id'))
        if uid not in self.friends or str(event.get('self_id'))!=self.account:
            return
        if self.settings['friends']=='owner' and uid!=str(self.cfg.owner):
            return
        conversation = await self.ensure(uid)
        conversation.last_event = time.monotonic()
        text = plain_text(event.get('message'))
        kind, tail = control(text)
        if conversation.learning or kind or (text and text.startswith('/') and not text.startswith('/search ')):
            conversation.interrupt()
        if kind=='stop' and not tail:
            conversation.control_text = '已停止刚才的任务。'

    async def maintenance(self):
        while True:
            await asyncio.sleep(5)
            for c in list(self.conversations.values()):
                if c.task.done() and time.monotonic() >= self.recovery_at.get(c.cid,0):
                    self.recovery_at[c.cid] = time.monotonic()+30
                    try:
                        await self.ensure(str(c.uid))
                        log.info('Conversation worker recovered')
                    except Exception as exc:
                        log.warning('Conversation recovery pending (%s)',type(exc).__name__)
                    continue
                if c.cid in self.rotate and c.turn_task is None:
                    self.rotate.discard(c.cid)
                    await self.remove(c)
                    await self.ensure(str(c.uid))
                elif c.uid!=self.cfg.owner and not c.turn_task and time.monotonic()-c.last_event>self.settings['idle_seconds']:
                    await self.remove(c)

    async def run(self):
        if not all(m.supports_tools for m in self.cfg.models.values()):
            raise ValueError('Tulpa resident agents require models with native tools support')
        await self.health.start()
        await self.relay.start()
        self.tasks = [asyncio.create_task(self.transport.run(lambda _:False,on_event=self.relay.receive)),
                      asyncio.create_task(self.transport.monitor()),
                      asyncio.create_task(self.relay.publish()),asyncio.create_task(self.relay.watch()),
                      asyncio.create_task(self.health.heartbeat())]
        try:
            self.health.stage = 'waiting_qq_login'
            while True:
                try:
                    await self.directory()
                    break
                except (DeliveryError,TypeError,KeyError):
                    log.info('Waiting for QQ login and friend directory')
                    await asyncio.sleep(5)
            self.health.stage = 'starting_framework'
            root = Path(os.environ.get('TULPA_ROOT',Path(self.cfg.database).parent/'tulpa'))
            self.backend = Backend(root,self.relay,port=int(os.environ.get("TULPA_MCP_PORT","18777")))
            policy = dict(quiet_seconds=max(.1,self.cfg.message_merge_seconds),
                          max_collect_seconds=self.cfg.message_merge_max_seconds)
            (root/'data/mcp-chat-policy.json').write_text(json.dumps(policy))
            await self.backend.start()
            self.relay.on_event = self.event
            # Attach/resume every persisted active session, bounded by Tulpa's
            # own eight-session limit. No pre-session messages are fabricated.
            with self.backend.access.connect() as db:
                active=[dict(r) for r in db.execute('SELECT id,grant_id,conversation_id FROM chat_sessions WHERE active=1')]
            owned={x['id'] for x in self.backend.credentials.values()}
            for row in active:
                cid=row['conversation_id']
                if row['grant_id'] not in owned:continue
                parts=cid.split(':')
                allowed=(len(parts)==3 and parts[:2]==[self.account,'direct'] and parts[2] in self.friends
                         and (self.settings['friends']=='all' or parts[2]==str(self.cfg.owner)))
                if allowed and len(self.conversations)<self.settings['max_sessions']:
                    await self.ensure(parts[2])
                else:
                    await asyncio.to_thread(self.backend.service.tools.chat.stop,row['id'])
            if str(self.cfg.owner) in self.friends:

                await self.ensure(str(self.cfg.owner))
            self.tasks.append(asyncio.create_task(self.maintenance()))
            self.health.stage = 'running'
            log.info('QQbot Tulpa runtime ready; MCP=%s', self.backend.url)
            await asyncio.gather(*self.tasks)
        finally:
            self.health.stage = 'stopping'
            for c in list(self.conversations.values()):
                c.task.cancel()
            await asyncio.gather(*(c.task for c in self.conversations.values()),return_exceptions=True)
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
            if self.backend:
                await self.backend.close()
            await self.relay.close()
            await self.health.close()
            self.store.close()
