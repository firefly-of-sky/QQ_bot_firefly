import asyncio
import json
import logging
import uuid

import aiohttp

log = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


class OneBot:
    """One receive loop resolves action responses independently of the chat worker."""

    def __init__(self, http: aiohttp.ClientSession, url: str, token: str):
        self.http, self.url, self.token = http, url, token
        self.ws = None
        self.pending = {}
        self.queue = asyncio.Queue(maxsize=100)

    async def send(self, user_id: int, text: str):
        ws = self.ws
        if ws is None or ws.closed:
            raise DeliveryError("QQ connection unavailable")
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        try:
            await ws.send_json({"action": "send_private_msg", "params": {
                "user_id": user_id, "message": [{"type": "text", "data": {"text": text}}]}, "echo": echo})
            response = await asyncio.wait_for(future, timeout=15)
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise DeliveryError("QQ rejected message")
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise DeliveryError("QQ delivery failed or unconfirmed") from exc
        finally:
            self.pending.pop(echo, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve disconnect exceptions if send itself failed.

    async def run(self, accept):
        delay = 1
        while True:
            try:
                async with self.http.ws_connect(self.url, headers={"Authorization": f"Bearer {self.token}"},
                                                heartbeat=30, max_msg_size=1024 * 1024) as ws:
                    self.ws = ws
                    log.info("QQ WebSocket connected")
                    delay = 1
                    async for frame in ws:
                        if frame.type != aiohttp.WSMsgType.TEXT:
                            if frame.type == aiohttp.WSMsgType.ERROR:
                                break
                            continue
                        try:
                            event = json.loads(frame.data)
                        except (ValueError, TypeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        echo = event.get("echo")
                        if isinstance(echo, str) and echo in self.pending:
                            future = self.pending[echo]
                            if not future.done():
                                future.set_result(event)
                        elif accept(event):
                            try:
                                self.queue.put_nowait(event)
                            except asyncio.QueueFull:
                                log.warning("Message queue full; incoming event dropped")
            except (aiohttp.ClientError, OSError, TimeoutError):
                # Do not log exception text: URLs, headers and payloads may contain secrets.
                log.warning("QQ connection failed; retry in %s seconds", delay)
            finally:
                self.ws = None
                for future in list(self.pending.values()):
                    if not future.done():
                        future.set_exception(DeliveryError("QQ disconnected"))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
