"""Bootstrap real Tulpa, with persistent data outside the vendored source tree."""
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys


def load_upstream(root):
    os.environ['TULPA_ROOT'] = str(Path(root).resolve())
    source = Path(os.environ.get('TULPA_SOURCE', Path(__file__).resolve().parents[2]/'vendor/tulpa'))
    if not (source/'chatlocal/mcp_server.py').is_file():
        raise RuntimeError('Tulpa source missing; restore the pinned vendor/tulpa snapshot')
    sys.path.insert(0, str(source))


def secret_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Backend:
    def __init__(self, root, relay, port=18777):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        load_upstream(self.root)
        os.environ.update(REPLY_ONEBOT_URL=f'http://127.0.0.1:{relay.port}',
                          REPLY_ONEBOT_WS_URL=f'ws://127.0.0.1:{relay.port}/events',
                          REPLY_ONEBOT_TOKEN=relay.token, REPLY_ONEBOT_WS_TOKEN=relay.token)
        from chatlocal.store import Store
        from chatlocal.terminal_server import create_app
        self.admin_app = create_app(root=self.root, store=Store(self.root/'data/chats.sqlite3'))
        self.service = self.admin_app.state.tulpa_mcp
        self.access = self.service.access
        self.access.configure(True, port)
        self.credentials_path = self.root/'runner-connections.json'
        self.credentials = json.loads(self.credentials_path.read_text()) if self.credentials_path.exists() else {}

    async def start(self):
        await asyncio.to_thread(self.service.start)
        if not self.service.status()['running']:
            raise RuntimeError('Tulpa MCP failed to start: '+self.service.status().get('error',''))
        import uvicorn
        self.admin = uvicorn.Server(uvicorn.Config(self.admin_app, host='127.0.0.1', port=int(os.environ.get('TULPA_ADMIN_PORT','7861')),
                                                  access_log=False, log_level='warning'))
        self.admin.capture_signals = nullcontext
        self.admin_task = asyncio.create_task(self.admin.serve())

    async def grant(self, conversation_id):
        saved = self.credentials.get(conversation_id)
        if saved:
            # Revocation is a durable administrator decision. Never silently
            # replace a revoked/restricted credential when another message arrives.
            await asyncio.to_thread(self.access.authorize, saved['token'], source=False)
            return saved
        saved = await asyncio.to_thread(self.access.create, dict(
            name='QQbot '+conversation_id, platforms=['qq'],
            conversations=[['qq', conversation_id]], send=True, chat=True, chat_images=True))
        self.credentials[conversation_id] = saved
        secret_json(self.credentials_path, self.credentials)
        return saved

    @property
    def url(self):
        return self.service.status()['url']

    async def operator_grant(self, policy, owner_cid):
        """Enroll explicit host consent; never heal a revoked/reduced grant."""
        if not policy['enabled']:return None
        approved_flags={'chat_voice','chat_video','chat_files','qq_messages','qq_interactions',
                        'qq_friends','qq_profile','qq_qzone','qq_albums'}
        path=self.root/'operator-connection.json'
        saved=json.loads(path.read_text()) if path.exists() else None
        normal=await self.grant(owner_cid)
        if saved:await asyncio.to_thread(self.access.authorize,saved['token'],source=False)
        if not saved or saved.get('authorization_id')!=policy['authorization_id']:
            await asyncio.to_thread(self.access.update_permissions,normal['id'],dict(
                chat_sticker_send=True,chat_voice=True,chat_video=True,chat_files=True,
                qq_messages=True,qq_interactions=True))
            permissions=dict(send=True,chat=True,onebot=True,manage=True,chat_images=True,
                chat_sticker_send=True,chat_sticker_collect=True,**{k:True for k in approved_flags})
            if saved:await asyncio.to_thread(self.access.update_permissions,saved['id'],permissions)
            else:saved=await asyncio.to_thread(self.access.create,dict(name='QQbot owner operator',
                platforms=['qq'],all_conversations=True,**permissions))
            saved['authorization_id']=policy['authorization_id'];secret_json(path,saved)
        return saved

    async def close(self):
        if hasattr(self,'admin'):
            self.admin.should_exit = True
            await self.admin_task
        await asyncio.to_thread(self.service.stop)
