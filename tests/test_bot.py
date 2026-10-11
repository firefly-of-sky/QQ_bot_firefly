import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from qqbot.bot import Bot, split_reply
from qqbot.config import Config, Model, load
from qqbot.llm import LLM, ModelError, QQ_STYLE, messages
from qqbot.onebot import DeliveryError, OneBot
from qqbot.store import Store
from scripts.init_config import initialize


def config(path=":memory:"):
    return Config(owner=123, token="test-token", ws_url="ws://localhost:3001", database=path,
                  models={"first": Model("https://example.test/v1", "m1", "TEST_KEY"),
                          "second": Model("https://example.test/v1", "m2", "TEST_KEY")},
                  default_model="first")


def event(number=1, text="你好", **extra):
    return {"post_type": "message", "message_type": "private", "sub_type": "friend", "self_id": 456,
            "user_id": 123, "message_id": number,
            "message": [{"type": "text", "data": {"text": text}}], **extra}


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = AsyncMock()
        self.llm.complete.return_value = "你好！"
        self.transport = AsyncMock()
        self.bot = Bot(config(), self.store, self.llm, self.transport)

    async def asyncTearDown(self):
        self.store.close()

    async def test_conversation_and_duplicate(self):
        await self.bot.handle(event())
        await self.bot.handle(event())
        await self.bot.handle(event(2, "继续"))
        self.assertEqual(self.llm.complete.await_count, 2)
        conversation = self.llm.complete.call_args.args[1]
        self.assertEqual([m["role"] for m in conversation], ["system", "user", "assistant", "user"])
        self.assertEqual(conversation[1]["content"], "你好")

    async def test_unauthorized_group_self_and_temp_ignored(self):
        for extra in ({"user_id": None}, {"message_type": "group"}, {"self_id": 123},
                      {"sub_type": "group"}, {"post_type": "message_sent"}, {"message_id": None}):
            await self.bot.handle(event(**extra))
        self.llm.complete.assert_not_awaited()
        self.transport.send.assert_not_awaited()

    async def test_commands_switch_and_reset(self):
        await self.bot.handle(event())
        for num, cmd in enumerate(["/help", "/models", "/status", "/model missing"], 2):
            await self.bot.handle(event(num, cmd))
        self.assertEqual(len(self.store.history("private:456:123", 20)), 1)
        await self.bot.handle(event(6, "/model second"))
        self.assertEqual(self.store.model("private:456:123", "first"), "second")
        self.assertEqual(self.store.history("private:456:123", 20), [])
        await self.bot.handle(event(7))
        self.assertEqual(self.llm.complete.call_args.args[0].model, "m2")
        await self.bot.handle(event(8, "/reset"))
        self.assertEqual(self.store.history("private:456:123", 20), [])

    async def test_unsupported_mixed_and_string_messages(self):
        for index, msg in enumerate(([{"type": "image", "data": {}}],
                                     [{"type": "text", "data": {"text": "看图"}}, {"type": "image", "data": {}}],
                                     "[CQ:image,file=foo]")):
            await self.bot.handle(event(index, message=msg))
        self.llm.complete.assert_not_awaited()
        self.assertEqual(self.transport.send.await_count, 3)

    async def test_failed_model_and_delivery_not_saved(self):
        self.llm.complete.side_effect = ModelError("模型回复超时，请稍后重试。")
        await self.bot.handle(event())
        self.assertEqual(self.store.history("private:456:123", 20), [])
        self.llm.complete.side_effect = None
        self.transport.send.side_effect = DeliveryError("disconnected")
        with self.assertRaises(DeliveryError):
            await self.bot.handle(event(2))
        self.assertEqual(self.store.history("private:456:123", 20), [])

    async def test_input_limit_and_removed_model(self):
        self.bot.cfg = replace(config(), max_input_chars=2)
        await self.bot.handle(event(text="三字长"))
        self.store.reset("private:456:123", "removed")
        await self.bot.handle(event(2))
        self.llm.complete.assert_not_awaited()

    async def test_serial_processing_and_chunk_order(self):
        self.bot.cfg = replace(config(), reply_chunk_chars=3)
        self.llm.complete.return_value = "一二\n三四五六"
        await asyncio.gather(self.bot.handle(event(1)), self.bot.handle(event(2)))
        self.assertEqual([call.args[1] for call in self.transport.send.call_args_list],
                         ["一二\n", "三四五", "六"] * 2)
        self.assertEqual(len(self.llm.complete.call_args.args[1]), 4)


class StoreAndContextTests(unittest.TestCase):
    def test_initializer_private_and_non_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath(".env.example").write_text("ONEBOT_TOKEN=replace-with-a-random-token\nWEBUI_TOKEN=replace-with-another-random-token\n")
            root.joinpath("config.example.toml").write_text('default_model="m"\n')
            with patch("builtins.print"):
                initialize(root)
                original = root.joinpath(".env").read_text()
                self.assertNotIn("replace-with", original)
                self.assertEqual(root.joinpath(".env").stat().st_mode & 0o777, 0o600)
                initialize(root)
                self.assertEqual(root.joinpath(".env").read_text(), original)

    def test_restart_retention_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db.sqlite3")
            store = Store(path)
            store.reset("s", "second")
            for i in range(25):
                store.append("s", str(i), "ok", 20)
            self.assertTrue(store.claim("e"))
            store.close()
            store = Store(path)
            self.assertEqual(store.model("s", "first"), "second")
            self.assertFalse(store.claim("e"))
            history = store.history("s", 20)
            self.assertEqual(len(history), 20)
            self.assertEqual(history[0][0], "5")
            store.reset("s")
            self.assertEqual(store.history("s", 20), [])
            store.close()

    def test_budget_complete_pairs_and_unicode(self):
        cfg = replace(config(), system_prompt="s", context_budget_bytes=136 + len(QQ_STYLE.encode("utf-8")))
        result = messages(cfg, [("old", "old"), ("u", "a")], "你")
        self.assertEqual([m["content"] for m in result], ["s" + QQ_STYLE, "u", "a", "你"])
        with self.assertRaises(ModelError):
            messages(replace(cfg, context_budget_bytes=1), [], "你")
        result = messages(config(), [(str(i), "a") for i in range(30)], "last")
        self.assertEqual(len(result), 42)
        for text in ["a" * 4000, "a\nb\n" * 500, "你好😊" * 100]:
            parts = split_reply(text, 17)
            self.assertEqual("".join(parts), text)
            self.assertTrue(all(0 < len(p) <= 17 for p in parts))

    def test_config_and_missing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('default_model="m"\n[models.m]\nbase_url="https://example.test/v1"\nmodel="test"\napi_key_env="TEST_KEY"\n')
            with patch.dict(os.environ, {"CONFIG_PATH": str(path), "OWNER_QQ": "123", "ONEBOT_TOKEN": "abc", "TEST_KEY": "secret"}):
                self.assertEqual(load().default_model, "m")
                with patch.dict(os.environ, {"TEST_KEY": ""}):
                    with self.assertRaises(ValueError):
                        load()
                valid = path.read_text()
                for invalid in ('history_turns=2.5\n', 'request_timeout_seconds=nan\n', 'max_concurrent_requests=0\n', 'requests_per_minute=-1\n'):
                    path.write_text(invalid + valid)
                    with self.assertRaises(ValueError):
                        load()


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runners = []
        self.http = aiohttp.ClientSession()
        self.env = patch.dict(os.environ, {"TEST_KEY": "test-secret"})
        self.env.start()

    async def asyncTearDown(self):
        await self.http.close()
        for runner in self.runners:
            await runner.cleanup()
        self.env.stop()

    async def server(self, handler, path):
        app = web.Application()
        app.router.add_route("*", path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.runners.append(runner)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def test_model_http_errors_timeout_and_payload(self):
        status, mode = 200, "ok"
        received = []

        async def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            received.append(await request.json())
            if mode == "slow":
                await asyncio.sleep(0.1)
            if mode == "bad":
                return web.json_response({"choices": []})
            return web.json_response({"choices": [{"message": {"content": "reply"}, "finish_reason": "length" if mode == "length" else "stop"}]}, status=status)

        url = await self.server(handler, "/v1/chat/completions")
        model = Model(url + "/v1", "test-model", "TEST_KEY")
        llm = LLM(self.http, config())
        self.assertEqual(await llm.complete(model, messages(config(), [], "hello")), "reply")
        self.assertEqual(received[0]["model"], "test-model")
        self.assertFalse(received[0]["stream"])
        vision = messages(config(), [], "看图", ["data:image/png;base64,dGVzdA=="])
        self.assertEqual(await llm.complete(model, vision), "reply")
        self.assertEqual(received[-1]["messages"][-1]["content"][1]["type"], "image_url")
        status = 400
        with self.assertRaisesRegex(ModelError, "未接受图片"):
            await llm.complete(model, vision)
        for status, expected in [(401, "鉴权"), (403, "鉴权"), (429, "受限"), (500, "HTTP 500")]:
            with self.assertRaisesRegex(ModelError, expected):
                await llm.complete(model, [])
        status, mode = 200, "length"
        self.assertIn("达到长度限制", await llm.complete(model, []))
        status, mode = 200, "bad"
        with self.assertRaisesRegex(ModelError, "格式异常"):
            await llm.complete(model, [])
        mode = "slow"
        llm.cfg = replace(config(), request_timeout_seconds=0.01)
        with self.assertRaisesRegex(ModelError, "超时"):
            await llm.complete(model, [])
        broken = Model("http://127.0.0.1:1/v1", "m", "TEST_KEY")
        with self.assertRaisesRegex(ModelError, "无法连接"):
            await llm.complete(broken, [])

    async def test_qq_reconnect_ack_dedup_end_to_end(self):
        connections, replies = [], []
        delivered = asyncio.Event()

        async def qq(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            connections.append(ws)
            await ws.send_json(event(1))  # same message replayed after reconnect
            if len(connections) > 1:
                await ws.send_json(event(2, "继续"))
            async for frame in ws:
                action = frame.json()
                self.assertEqual(action["action"], "send_private_msg")
                self.assertEqual(action["params"]["user_id"], 123)
                replies.append(action["params"]["message"][0]["data"]["text"])
                await ws.send_json({"echo": action["echo"], "status": "ok", "retcode": 0})
                if len(connections) == 1:
                    await ws.close()
                else:
                    delivered.set()
            return ws

        async def model_api(request):
            data = await request.json()
            return web.json_response({"choices": [{"message": {"content": "回答：" + data["messages"][-1]["content"]}}]})

        model_url = await self.server(model_api, "/v1/chat/completions")
        url = await self.server(qq, "/ws")
        cfg = replace(config(), models={"first": Model(model_url + "/v1", "m", "TEST_KEY")})
        transport = OneBot(self.http, url.replace("http://", "ws://") + "/ws", cfg.token)
        store = Store(":memory:")
        bot = Bot(cfg, store, LLM(self.http, cfg), transport)
        tasks = [asyncio.create_task(transport.run(bot.accepts)), asyncio.create_task(bot.worker())]
        try:
            await asyncio.wait_for(delivered.wait(), 5)
            await asyncio.wait_for(transport.queue.join(), 2)
            self.assertEqual(replies, ["回答：你好", "回答：继续"])
            self.assertEqual(len(store.history("private:456:123", 20)), 2)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.close()

    async def test_disconnect_rejects_inflight_action(self):
        async def qq(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for frame in ws:
                await ws.close()
            return ws
        url = await self.server(qq, "/ws")
        transport = OneBot(self.http, url.replace("http://", "ws://") + "/ws", "token")
        task = asyncio.create_task(transport.run(lambda e: False))
        try:
            async with asyncio.timeout(2):
                while transport.ws is None:
                    await asyncio.sleep(0.01)
            with self.assertRaises(DeliveryError):
                await transport.send(123, "hello")
            self.assertEqual(transport.pending, {})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
