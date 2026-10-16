"""Authenticated loopback HTTP/WS bridge to NapCat's existing forward WS."""
import asyncio
import secrets
import logging

from aiohttp import web

from ..onebot import DeliveryError


class Relay:
    def __init__(self, transport, token=None, port=0):
        self.transport = transport
        self.token = token or secrets.token_urlsafe(32)
        self.port = port
        self.clients = set()
        self.runner = None
        self.on_event = None
        self.events = asyncio.Queue(maxsize=512)
        self.losses = 0

    def receive(self, event):
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            self.losses += 1
            # Fail closed: disconnect downstream so Tulpa invalidates old plans.
            for client in list(self.clients):
                asyncio.create_task(client.close(code=1013))

    async def publish(self):
        while True:
            event = await self.events.get()
            try:
                if self.on_event:
                    try:
                        await self.on_event(event)
                    except Exception as exc:
                        logging.getLogger(__name__).warning('QQ event routing failed (%s)', type(exc).__name__)
                        # Continue forwarding the authentic event to existing
                        # scoped sessions. A new friend's capacity/bootstrap
                        # failure must not disconnect every active conversation.
                for client in list(self.clients):
                    try:
                        await asyncio.wait_for(client.send_json(event), 2)
                    except (ConnectionError, RuntimeError, TimeoutError):
                        await client.close()
            finally:
                self.events.task_done()

    async def start(self):
        @web.middleware
        async def auth(request, handler):
            if not secrets.compare_digest(request.headers.get('Authorization', '').encode(), ('Bearer '+self.token).encode()):
                raise web.HTTPUnauthorized()
            if request.headers.get('Origin'):
                raise web.HTTPForbidden()
            return await handler(request)
        app = web.Application(middlewares=[auth], client_max_size=8*1024*1024)
        app.router.add_get('/events', self.websocket)
        app.router.add_post('/{action}', self.action)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', self.port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def websocket(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            # Fresh probes verify the source; never replay an old healthy heartbeat.
            login = await self.transport.action('get_login_info', {})
            status = await self.transport.action('get_status', {})
            await ws.send_json(dict(post_type='meta_event', meta_event_type='heartbeat',
                                    self_id=login['user_id'], status=status))
            async for _ in ws:
                pass
        except (DeliveryError, KeyError, TypeError):
            await ws.close(code=1013)
        finally:
            self.clients.discard(ws)
        return ws

    async def action(self, request):
        try:
            params = await request.json()
            if not isinstance(params, dict):
                raise web.HTTPBadRequest()
            result = await self.transport.action(request.match_info['action'], params)
            return web.json_response(dict(status='ok', retcode=0, data=result))
        except DeliveryError:
            # Ambiguous send results must reach Tulpa as transport uncertainty,
            # never as a clean rejection that might invite a second write.
            raise web.HTTPServiceUnavailable(text='OneBot source unavailable') from None

    async def watch(self):
        while True:
            await asyncio.sleep(1)
            if self.transport.ws is None or self.transport.online is False:
                for client in list(self.clients):
                    await client.close(code=1013)

    async def close(self):
        for client in list(self.clients):
            await client.close()
        if self.runner:
            await self.runner.cleanup()
