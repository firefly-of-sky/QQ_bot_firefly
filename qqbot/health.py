"""Loopback health endpoints: process recovery is separate from QQ re-login."""
import argparse
import asyncio
import json
import os
import time
import urllib.error
import urllib.request

from aiohttp import web

from . import __version__


class RuntimeHealth:
    def __init__(self, app):
        self.app = app
        self.stage = 'starting'
        self.started = time.monotonic()
        self.pulse = self.started
        self.runner = None
        self.port = int(os.environ.get('QQBOT_HEALTH_PORT', '18080'))

    def snapshot(self):
        app = self.app
        connected = app.transport.ws is not None and not app.transport.ws.closed
        backend = app.backend
        mcp = bool(backend and backend.service.status()['running'])
        receiver = backend.service.tools.chat.receiver.status()['state'] if backend else 'waiting_login'
        conversations = list(app.conversations.values())
        failed = sum(bool(c.error or (c.task and c.task.done())) for c in conversations)
        ready_workers = sum(bool(c.ready.is_set() and not c.error and c.task and not c.task.done()) for c in conversations)
        live = time.monotonic()-self.pulse < 30 and self.stage != 'stopping'
        ready = live and connected and app.transport.online is True and mcp and receiver == 'connected' and not failed
        if not live:
            reason = 'process_unresponsive'
        elif app.transport.online is False:
            reason = 'qq_login_required'
        elif not connected or app.transport.online is not True:
            reason = 'qq_connection_pending'
        elif not mcp or receiver != 'connected':
            reason = 'framework_connection_pending'
        elif failed:
            reason = 'conversation_recovering'
        else:
            reason = 'ready'
        return dict(version=__version__, live=live, ready=ready, reason=reason,
                    stage=self.stage, uptime_seconds=round(time.monotonic()-self.started),
                    qq_connected=connected, qq_online=app.transport.online,
                    mcp_running=mcp, receiver=receiver,
                    conversations=dict(total=len(conversations), ready=ready_workers, failed=failed),
                    relay_dropped_events=app.relay.losses)

    async def start(self):
        async def status(request):
            value = self.snapshot()
            kind = request.match_info['kind']
            ok = value['live'] if kind == 'live' else value['ready'] if kind == 'ready' else True
            return web.json_response(value, status=200 if ok else 503, headers={'Cache-Control':'no-store'})
        server = web.Application()
        server.router.add_get('/health/{kind:live|ready|status}', status)
        self.runner = web.AppRunner(server, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', self.port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def heartbeat(self):
        while True:
            self.pulse = time.monotonic()
            await asyncio.sleep(2)

    async def close(self):
        self.stage = 'stopping'
        if self.runner:
            await self.runner.cleanup()


def probe(kind='live', *, port=None):
    port = port or int(os.environ.get('QQBOT_HEALTH_PORT','18080'))
    # Health must never use ambient proxy settings or send provider credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/health/{kind}', timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.load(exc)
    except (OSError,ValueError):
        return 503, dict(live=False, ready=False, reason='health_endpoint_unavailable')


def main():
    parser = argparse.ArgumentParser(description='QQbot process/QQ readiness probe')
    parser.add_argument('--ready',action='store_true')
    parser.add_argument('--status',action='store_true')
    args = parser.parse_args()
    status, value = probe('status' if args.status else 'ready' if args.ready else 'live')
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if status == 200 else 1)


if __name__ == '__main__':
    main()
