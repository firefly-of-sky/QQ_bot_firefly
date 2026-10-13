import asyncio
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from qqbot.agent import Agent, Context, request_bytes
from qqbot.bot import Bot
from qqbot.config import load
from qqbot.llm import LLM, Model, ModelError, ModelReply, ToolCall, parse_reply
from qqbot.progress import ProgressState
from qqbot.prompts import load_persona
from qqbot.search import Search, SearchUnavailable
from qqbot.store import Store
from qqbot.tools import Tool, ToolError, ToolRegistry, build_tools, calculate, encode_result, public_url
from qqbot.__main__ import load_env_file
from test_bot import config, event


def agent_config(**kwargs):
    cfg = config()
    return replace(cfg, models={k: replace(v, supports_tools=True) for k, v in cfg.models.items()}, **kwargs)


def call(number, name="calculate", **arguments):
    return ModelReply(calls=(ToolCall(str(number), name, json.dumps(arguments, ensure_ascii=False)),))


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = agent_config()
        self.llm = SimpleNamespace(turn=AsyncMock())
        self.search = SimpleNamespace(available=lambda: True, query_once=AsyncMock(), read_page=AsyncMock())
        self.state = ProgressState()
        self.registry = build_tools(self.cfg, self.search, self.state, web_enabled=True)
        self.agent = Agent(self.cfg, self.llm, self.registry)

    async def run_agent(self, text="问题", **kwargs):
        return await self.agent.run(self.cfg.models["first"], [], text, **kwargs)

    async def test_direct_chat_does_not_execute_tools(self):
        self.llm.turn.return_value = ModelReply("你好呀")
        result = await self.run_agent("你好")
        self.assertEqual((result.text, result.model_calls, result.tool_calls), ("你好呀", 1, 0))
        self.search.query_once.assert_not_awaited()

    async def test_search_revises_query_then_reads_original_and_answers(self):
        self.search.query_once.side_effect = [
            [{"url": "https://example.com/private", "title": "民办", "content": "只有民办学校资料"}],
            [{"url": "https://example.com/public", "title": "公办招聘", "content": "需读取正文"}],
        ]
        self.search.read_page.return_value = {"url": "https://example.com/public", "content": "岗位A月薪5000元"}
        scripted = [call(1, "web_search", query="某地教师待遇"),
                    call(2, "web_search", query="某地 公办教师 招聘 薪酬", depth="advanced"),
                    call(3, "read_page", url="https://example.com/public"), ModelReply("这个岗位月薪5000元[1]。")]
        seen = []

        async def turn(model, conversation, tools, choice):
            seen.append(copy.deepcopy(conversation))
            return scripted.pop(0)

        self.llm.turn.side_effect = turn
        result = await self.run_agent()
        self.assertEqual(result.text, "这个岗位月薪5000元。")
        self.assertEqual((result.model_calls, result.tool_calls), (4, 3))
        self.assertIn("只有民办学校资料", seen[1][-1]["content"])
        self.assertIn("岗位A月薪5000元", seen[3][-1]["content"])
        self.assertEqual(self.search.query_once.call_args_list[1].args, ("某地 公办教师 招聘 薪酬", "advanced"))

    async def test_invalid_arguments_return_error_then_model_repairs(self):
        self.llm.turn.side_effect = [ModelReply(calls=(ToolCall("1", "calculate", "{broken"),)),
                                     call(2, expression="17*29"), ModelReply("493")]
        result = await self.run_agent()
        self.assertEqual(result.text, "493")
        self.assertFalse(json.loads(self.llm.turn.call_args_list[1].args[1][-1]["content"])["ok"])
        self.assertEqual(json.loads(self.llm.turn.call_args_list[2].args[1][-1]["content"])["data"]["result"], 493)

    async def test_explicit_search_cannot_succeed_without_search_evidence(self):
        self.llm.turn.return_value = ModelReply("没查但答案是这样")
        with self.assertRaisesRegex(ModelError, "未获得有效搜索"):
            await self.run_agent(force_search=True)
        self.assertEqual(self.llm.turn.call_args.args[3], "auto")
        self.assertIn("本轮必须先调用 web_search", self.llm.turn.call_args.args[1][0]["content"])
        self.registry.tools.pop("web_search")
        with self.assertRaisesRegex(ModelError, "未启用"):
            await self.run_agent(force_search=True)

    async def test_budget_closes_loop_and_disallows_more_tools(self):
        self.agent.cfg = replace(self.cfg, agent_max_tool_calls=1)
        self.llm.turn.side_effect = [call(1, expression="1+1"), ModelReply("2")]
        result = await self.run_agent()
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(self.llm.turn.call_args.args[3], "none")
        self.llm.turn.side_effect = [call(3, expression="2+2"), call(4, expression="3+3")]
        with self.assertRaisesRegex(ModelError, "预算耗尽"):
            await self.run_agent()

    async def test_multi_call_response_does_not_exceed_budget(self):
        self.agent.cfg = replace(self.cfg, agent_max_tool_calls=1)
        first = ModelReply(calls=(ToolCall("a", "web_search", '{"query":"a"}'),
                                  ToolCall("b", "web_search", '{"query":"b"}')))
        self.search.query_once.return_value = [{"url": "https://example.com", "title": "a", "content": "证据"}]
        self.llm.turn.side_effect = [first, ModelReply("完成一部分")]
        await self.run_agent()
        self.search.query_once.assert_awaited_once()
        messages = self.llm.turn.call_args.args[1]
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ["a", "b"])
        self.assertIn("预算已用完", messages[-1]["content"])

    async def test_duplicate_call_id_is_rejected(self):
        self.llm.turn.side_effect = [call(1, expression="1+1"), call(1, expression="2+2")]
        with self.assertRaisesRegex(ModelError, "重复使用"):
            await self.run_agent()

    async def test_whole_run_timeout_cancels_model(self):
        self.agent.cfg = replace(self.cfg, agent_timeout_seconds=.02)
        cancelled = asyncio.Event()
        async def slow(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.llm.turn.side_effect = slow
        with self.assertRaisesRegex(ModelError, "总时限"):
            await self.run_agent()
        self.assertTrue(cancelled.is_set())

    async def test_failure_does_not_turn_into_unverified_search_answer(self):
        self.search.query_once.side_effect = SearchUnavailable("额度不足")
        self.llm.turn.side_effect = [call(1, "web_search", query="a"), ModelReply("未找到")]
        with self.assertRaisesRegex(ModelError, "未获得有效搜索"):
            await self.run_agent(force_search=True)

    async def test_failed_search_does_not_claim_sources_found_in_progress(self):
        self.state.stage = "thinking"
        self.search.query_once.side_effect = ModelError("搜索失败")
        async def turn(model, conversation, tools, choice):
            if conversation[-1]["role"] == "user":
                return call(1, "web_search", query="test")
            self.assertEqual(self.state.stage, "thinking")
            return ModelReply("这次没有查到资料")
        self.llm.turn.side_effect = turn
        await self.run_agent()


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_unknown_tool_auth_failure_and_schema_validation(self):
        search = SimpleNamespace(query_once=AsyncMock(return_value=[{"url": "https://example.com", "content": "资料"}]))
        registry = build_tools(agent_config(), search, ProgressState(), web_enabled=True)
        for n in range(2):
            result = await registry.execute(call(n, "web_search", query="same").calls[0])
            self.assertTrue(json.loads(result)["ok"])
        search.query_once.assert_awaited_once()
        for item in [call(3, "send_qq", text="no"), call(4, "calculate", expression="2", extra="bad"),
                     call(5, "calculate", expression=2), call(6, "web_search", query="a", depth="bad")]:
            self.assertFalse(json.loads(await registry.execute(item.calls[0]))["ok"])
        search.query_once.side_effect = SearchUnavailable("密钥失效")
        await registry.execute(call(7, "web_search", query="new").calls[0])
        await registry.execute(call(8, "web_search", query="different").calls[0])
        self.assertEqual(search.query_once.await_count, 2)

    async def test_tool_timeout_and_cancel(self):
        registry = ToolRegistry(agent_config(tool_timeout_seconds=.01), ProgressState())
        stopped = asyncio.Event()
        async def slow(args):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        registry.register(Tool("slow", "test", {}, (), slow))
        result = json.loads(await registry.execute(call(1, "slow").calls[0]))
        self.assertFalse(result["ok"])
        self.assertTrue(stopped.is_set())
        task = asyncio.create_task(registry.execute(call(2, "slow").calls[0]))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    def test_calculator_limits_and_no_code_execution(self):
        self.assertEqual(calculate("(17*29 + 7) / 10"), 50)
        for expression in ("__import__('os').system('whoami')", "2**100000", "True", "[1][0]", "1/0", "1e309", "(-1)**.5"):
            with self.subTest(expression=expression), self.assertRaises(ToolError):
                calculate(expression)

    def test_public_urls_and_json_budget(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1/a", "http://[::1]/", "http://localhost/", "http://10.0.0.1/", "https://user:pass@example.com/", "http://example.com:999/"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                public_url(url)
        self.assertEqual(public_url("https://example.com/a#part"), "https://example.com/a")
        result = encode_result({"ok": True, "data": "汉字\\\"" * 5000}, 1024)
        self.assertLessEqual(len(result.encode()), 1024)
        self.assertTrue(json.loads(result)["truncated"])

    def test_context_eviction_keeps_tool_exchange_complete(self):
        context = Context([{"role": "system", "content": "s"},
                           {"role": "user", "content": "old" * 100}, {"role": "assistant", "content": "a"},
                           {"role": "user", "content": "current"}], 700)
        for n in range(2):
            reply = call(n, expression="1+1")
            context.append(reply, [{"role": "tool", "tool_call_id": str(n), "content": "data" * 50}])
        messages = context.fit([])
        self.assertLessEqual(request_bytes(messages, []), 700)
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "tool"])
        self.assertEqual(messages[-2]["tool_calls"][0]["id"], messages[-1]["tool_call_id"])

    def test_persona_reloads_and_env_never_executes_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "role.md"
            path.write_text("角色A")
            self.assertEqual(load_persona(str(path)), "角色A")
            path.write_text("角色B")
            self.assertEqual(load_persona(str(path)), "角色B")
            path.write_text("a" * 16385)
            with self.assertRaises(ValueError):
                load_persona(str(path))
            path.write_text("TEST_QQBOT_LITERAL='$(whoami)`test`'\n")
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(str(path))
                self.assertEqual(os.environ["TEST_QQBOT_LITERAL"], "$(whoami)`test`")


class AgentBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.llm = SimpleNamespace(turn=AsyncMock())
        self.transport = SimpleNamespace(send=AsyncMock(return_value=99), get_message=AsyncMock())
        self.search = SimpleNamespace(available=lambda: True, query_once=AsyncMock(), read_page=AsyncMock())
        self.bot = Bot(agent_config(progress_delay_seconds=5), self.store, self.llm, self.transport, search=self.search)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_only_final_answer_sent_and_history_isolated(self):
        self.llm.turn.side_effect = [ModelReply("我先算算", call(1, expression="17*29").calls),
                                     ModelReply("493"), ModelReply("你好")]
        await self.bot.handle(event(1, "算一下"))
        self.transport.send.assert_awaited_once_with(123, "493")
        await self.bot.handle(event(1, "另一个人", user_id=999))
        self.assertNotIn("493", json.dumps(self.llm.turn.call_args.args[1]))
        self.assertEqual(self.store.history("private:456:123", 20), [("算一下", "493")])

    async def test_cancel_and_recall_interrupt_tool_without_sending(self):
        for index, cancel in enumerate((True, False)):
            started, stopped = asyncio.Event(), asyncio.Event()
            async def search(*args):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            self.search.query_once.side_effect = search
            self.llm.turn.return_value = call(index, "web_search", query="test")
            number = index * 10 + 1
            task = asyncio.create_task(self.bot.handle(event(number, "请查一下")))
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                await self.bot.handle(event(number + 1, "/cancel"))
            else:
                await self.bot.handle({"post_type": "notice", "notice_type": "friend_recall",
                                       "self_id": 456, "user_id": 123, "message_id": number})
            await asyncio.wait_for(task, 1)
            self.assertTrue(stopped.is_set())
        self.transport.send.assert_awaited_once_with(123, "已取消当前及排队的问题。")
        self.assertEqual(self.store.history("private:456:123", 20), [])

    async def test_agent_does_not_use_legacy_keyword_routing(self):
        self.llm.turn.return_value = ModelReply("需要具体说明哪种待遇")
        await self.bot.handle(event(1, "工资"))
        self.search.query_once.assert_not_awaited()
        self.assertEqual(self.llm.turn.call_args.args[3], "auto")

    async def test_image_quote_survives_tool_turn_without_persisting_media(self):
        from test_images import picture
        image = "data:image/png;base64,dGVzdA=="
        self.bot.image_loader = SimpleNamespace(load=AsyncMock(return_value=[image]))
        self.llm.turn.side_effect = [call(1, expression="1+2"), ModelReply("图中算式等于3"), ModelReply("换句话说是三")]
        await self.bot.handle(event(1, message=[picture(), {"type": "text", "data": {"text": "算图中的题"}}]))
        messages = self.llm.turn.call_args.args[1]
        self.assertEqual(messages[1]["content"][1]["image_url"]["url"], image)
        await self.bot.handle(event(2, message=[{"type": "reply", "data": {"id": "99"}},
                                               {"type": "text", "data": {"text": "解释一下"}}]))
        self.assertIn("图中算式等于3", self.llm.turn.call_args.args[1][-1]["content"])
        self.assertNotIn("base64", str(self.store.history("private:456:123", 20)))

    async def test_web_permission_is_hidden_but_explicit_search_can_enable_it(self):
        self.bot.cfg = replace(self.bot.cfg, auto_search=False)
        self.llm.turn.return_value = ModelReply("你好")
        await self.bot.handle(event(1))
        schemas = self.llm.turn.call_args.args[2]
        self.assertEqual({s["function"]["name"] for s in schemas}, {"calculate", "current_time"})
        self.search.query_once.return_value = [{"url": "https://example.com", "title": "test", "content": "证据"}]
        self.llm.turn.side_effect = [call(1, "web_search", query="test"), ModelReply("有资料")]
        await self.bot.handle(event(2, "/search test"))
        self.search.query_once.assert_awaited_once_with("test", "basic")

    async def test_concurrent_users_have_separate_tool_caches_and_contexts(self):
        first_started, release = asyncio.Event(), asyncio.Event()
        async def search(query, depth):
            if query == "A":
                first_started.set()
                await release.wait()
            return [{"url": "https://example.com/" + query, "title": query, "content": "证据" + query}]
        async def turn(model, conversation, tools, choice):
            question = next(m["content"] for m in conversation if m["role"] == "user")
            if conversation[-1]["role"] == "user":
                return call(1, "web_search", query=question)
            data = json.loads(conversation[-1]["content"])
            self.assertEqual(data["data"]["results"][0]["content"], "证据" + question)
            return ModelReply("回答" + question)
        self.search.query_once.side_effect = search
        self.llm.turn.side_effect = turn
        first = asyncio.create_task(self.bot.handle(event(1, "A")))
        try:
            await asyncio.wait_for(first_started.wait(), 1)
            await self.bot.handle(event(1, "B", user_id=999))
            self.transport.send.assert_awaited_once_with(999, "回答B")
            release.set()
            await asyncio.wait_for(first, 1)
            self.assertEqual(self.search.query_once.await_count, 2)
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)


class ToolHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_search_works_with_endpoint_rejecting_named_tool_choice(self):
        requests = []
        async def handler(request):
            body = await request.json()
            requests.append(body)
            if isinstance(body.get("tool_choice"), dict):
                return web.json_response({"error": "named tool choice unsupported"}, status=400)
            if len(requests) == 1:
                reply = call(1, "web_search", query="Python asyncio")
                return web.json_response({"choices": [{"message": reply.as_message()}]})
            self.assertTrue(json.loads(body["messages"][-1]["content"])["ok"])
            return web.json_response({"choices": [{"message": {"content": "已查到官方资料"}}]})
        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            cfg = agent_config()
            search = SimpleNamespace(query_once=AsyncMock(return_value=[
                {"url": "https://docs.python.org/3/library/asyncio.html", "title": "asyncio", "content": "官方文档"}]))
            registry = build_tools(cfg, search, ProgressState(), web_enabled=True)
            with patch.dict(os.environ, {"TEST_KEY": "secret"}):
                async with aiohttp.ClientSession() as http:
                    model = Model(f"http://127.0.0.1:{port}", "test", "TEST_KEY")
                    result = await Agent(cfg, LLM(http, cfg), registry).run(model, [], "Python asyncio", force_search=True)
            self.assertEqual(result.text, "已查到官方资料")
            search.query_once.assert_awaited_once_with("Python asyncio", "basic")
            self.assertEqual(requests[0]["tool_choice"], "auto")
        finally:
            await runner.cleanup()

    async def test_actual_http_tool_request_result_and_final_answer(self):
        requests = []
        async def handler(request):
            body = await request.json()
            requests.append(body)
            if len(requests) == 1:
                message = {"content": None, "reasoning_content": "private thinking", "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "calculate", "arguments": '{"expression":"17*29"}'}}]}
                return web.json_response({"choices": [{"message": message, "finish_reason": "tool_calls"}]})
            self.assertEqual(body["messages"][-2]["reasoning_content"], "private thinking")
            self.assertEqual(json.loads(body["messages"][-1]["content"])["data"]["result"], 493)
            return web.json_response({"choices": [{"message": {"content": "结果是493"}, "finish_reason": "stop"}]})
        app = web.Application()
        app.router.add_post("/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            cfg = agent_config()
            model = Model(f"http://127.0.0.1:{port}", "test", "TEST_KEY")
            with patch.dict(os.environ, {"TEST_KEY": "secret"}):
                async with aiohttp.ClientSession() as http:
                    registry = build_tools(cfg, None, ProgressState(), web_enabled=False)
                    result = await Agent(cfg, LLM(http, cfg), registry).run(model, [], "17*29")
            self.assertEqual(result.text, "结果是493")
            self.assertEqual(requests[0]["tools"][0]["function"]["name"], "calculate")
            self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_1")
        finally:
            await runner.cleanup()

    def test_response_validation_prevents_truncated_or_duplicate_execution(self):
        raw = {"id": "one", "type": "function", "function": {"name": "calculate", "arguments": "{}"}}
        with self.assertRaises(ValueError):
            parse_reply({"choices": [{"message": {"tool_calls": [raw, raw]}}]})
        with self.assertRaisesRegex(ModelError, "长度限制"):
            parse_reply({"choices": [{"message": {"tool_calls": [raw]}, "finish_reason": "length"}]})
        reply = parse_reply({"choices": [{"message": {"content": None, "tool_calls": [raw]}}]})
        self.assertEqual(reply.calls[0].id, "one")


class AgentConfigTests(unittest.TestCase):
    def test_new_settings_reject_invalid_values(self):
        base = 'default_model="m"\n[models.m]\nbase_url="https://example.com/v1"\nmodel="test"\napi_key_env="TEST_KEY"\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            with patch.dict(os.environ, {"CONFIG_PATH": str(path), "OWNER_QQ": "123", "ONEBOT_TOKEN": "token", "TEST_KEY": "key"}):
                for invalid in ("agent_max_rounds=0", "agent_max_rounds=21", "agent_max_tool_calls=33",
                                "agent_timeout_seconds=nan", "tool_timeout_seconds=-1", "tool_result_bytes=50", "persona_file=42"):
                    path.write_text(invalid + "\n" + base)
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        load()
                path.write_text(base)
                self.assertTrue(load().models["m"].supports_tools)
