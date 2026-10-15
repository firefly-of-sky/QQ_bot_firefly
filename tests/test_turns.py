import asyncio
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from qqbot.bot import Bot
from qqbot.config import Model, load
from qqbot.llm import LLM, ModelReply, ToolCall
from qqbot.onebot import OneBot
from qqbot.store import Store
from qqbot.turns import control, plain_text
from test_bot import config, event


def recalled(number, user_id=123):
    return {'post_type': 'notice', 'notice_type': 'friend_recall', 'self_id': 456,
            'user_id': user_id, 'message_id': number}


def picture():
    return {'type': 'image', 'data': {'url': 'https://qpic.cn/test.png'}}


class TurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Store(':memory:')
        self.llm = AsyncMock()
        self.llm.complete.return_value = '回答'
        self.transport = AsyncMock()
        self.transport.send.side_effect = lambda *a: 1000 + self.transport.send.await_count
        self.loader = AsyncMock()
        self.loader.load.return_value = ['data:image/png;base64,dGVzdA==']
        self.bot = Bot(replace(config(), message_merge_seconds=.03, message_merge_max_seconds=.1),
                       self.db, self.llm, self.transport, self.loader)
        self.tasks = []

    async def asyncTearDown(self):
        await self.bot.close()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.db.close()

    def send(self, number, text='你好', **kwargs):
        task = asyncio.create_task(self.bot.handle(event(number, text, **kwargs)))
        self.tasks.append(task)
        return task

    async def pending(self):
        async with asyncio.timeout(1):
            while not self.bot.pending_turns:
                await asyncio.sleep(0)

    def last_text(self):
        return self.llm.complete.call_args.args[1][-1]['content']

    async def test_burst_one_request_order_dedup_history_and_source_recall(self):
        self.send(1, '帮我看看')
        await self.pending()
        self.send(2, '第三题')
        self.send(2, '重复事件不应加入')
        self.send(3, '请给步骤')
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_awaited_once()
        self.assertEqual(self.last_text(), '帮我看看\n第三题\n请给步骤')
        self.assertEqual(len(self.db.history('private:456:123', 20)), 1)
        self.assertEqual(self.db.source_ids('private:456:123'), {'1', '2', '3'})
        self.assertEqual(len(self.bot.requests['private:456:123']), 1)
        await self.bot.handle(recalled(2))
        self.assertEqual(self.db.history('private:456:123', 20), [])

    async def test_image_and_followup_in_same_burst(self):
        self.send(1, message=[picture()])
        await self.pending()
        self.send(2, '看左侧颜色')
        await asyncio.gather(*self.tasks)
        self.loader.load.assert_awaited_once_with(['https://qpic.cn/test.png'])
        self.assertEqual(self.last_text()[0]['text'], '看左侧颜色')
        self.assertEqual(self.transport.send.await_count, 1)

    async def test_recall_buffered_fragment_keeps_other_messages(self):
        self.send(1, '保留问题')
        await self.pending()
        self.send(2, '应撤回片段')
        await asyncio.sleep(0)
        await self.bot.handle(recalled(2))
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.last_text(), '保留问题')
        self.assertEqual(self.db.source_ids('private:456:123'), {'1'})

    async def test_cancel_buffer_and_cleanup_even_before_task_start(self):
        self.send(1)
        await asyncio.sleep(0)
        await self.bot.handle(event(2, '/cancel'))
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_not_awaited()
        self.assertFalse(self.bot.jobs)
        self.assertFalse(self.bot.pending_turns)
        self.assertFalse(self.bot.unfinished)

    async def test_buffered_correction_preserves_question(self):
        self.send(1, '比较北京和杭州的气候')
        await self.pending()
        self.send(2, '我刚才说错了，把北京改成上海')
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_awaited_once()
        self.assertIn('比较北京和杭州', self.last_text())
        self.assertIn('改成上海', self.last_text())
        self.assertEqual(self.db.source_ids('private:456:123'), {'1', '2'})

    async def test_running_correction_keeps_consumed_image_and_original_question(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def model(*args):
            if self.llm.complete.await_count == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return '新回答'
        self.llm.complete.side_effect = model
        # Exercise the old image cache as well as images carried in the event.
        self.bot.cfg = replace(self.bot.cfg, message_merge_seconds=0)
        await self.bot.handle(event(1, message=[picture()]))
        self.send(2, '请看图片右侧')
        await started.wait()
        self.bot.cfg = replace(self.bot.cfg, message_merge_seconds=.03)
        self.send(3, '我刚才说错了，是左侧')
        await asyncio.gather(*self.tasks)
        self.assertTrue(cancelled.is_set())
        content = self.last_text()
        self.assertIn('请看图片右侧', content[0]['text'])
        self.assertIn('是左侧', content[0]['text'])
        self.assertEqual(content[1]['type'], 'image_url')
        self.assertEqual(self.db.source_ids('private:456:123'), {'1', '2', '3'})
        self.assertEqual([c.args[1] for c in self.transport.send.call_args_list], ['新回答'])

    async def test_consecutive_corrections_keep_all_context(self):
        started = asyncio.Event()
        async def model(*args):
            if self.llm.complete.await_count == 1:
                started.set()
                await asyncio.Event().wait()
            return '最终'
        self.llm.complete.side_effect = model
        self.send(1, '查北京明天的天气')
        await started.wait()
        self.send(2, '我刚才说错了，是上海')
        await self.pending()
        self.send(3, '更正一下，是后天')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 2)
        for word in ('北京', '上海', '后天'):
            self.assertIn(word, self.last_text())
        self.assertEqual(self.db.source_ids('private:456:123'), {'1', '2', '3'})

    async def test_natural_stop_cancels_active_and_queue_without_late_answer(self):
        started = asyncio.Event()
        async def stubborn(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return '旧任务即使忽略取消也不能发送'
        self.llm.complete.side_effect = stubborn
        self.send(1, '很慢的问题')
        await started.wait()
        self.send(2, '排队的问题')
        await self.pending()
        await self.bot.handle(event(3, '不用查了，谢谢'))
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 1)
        self.assertEqual(self.transport.send.await_count, 1)
        self.assertIn('已经停下', self.transport.send.call_args.args[1])
        self.assertEqual(self.db.history('private:456:123', 20), [])

    async def test_stop_with_new_topic_only_answers_new_request(self):
        self.send(1, '旧问题')
        await self.pending()
        self.send(2, '不用查了，帮我算17加29')
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_awaited_once()
        self.assertEqual(self.last_text(), '帮我算17加29')

    async def test_quoted_stop_and_reported_speech_are_normal_material(self):
        self.bot.cfg = replace(self.bot.cfg, message_merge_seconds=0)
        for num, text in enumerate(('他说不用查了是什么意思？', '“不用查了”是什么意思？',
                                    '不用查了是什么意思？', '不要取消搜索，我需要结果'), 1):
            await self.bot.handle(event(num, text))
        self.bot.references.remember('private:456:123', 50, [{'type': 'text', 'data': {'text': '原文'}}])
        await self.bot.handle(event(10, message=[{'type': 'reply', 'data': {'id': '50'}},
                                                {'type': 'text', 'data': {'text': '不用查了'}}]))
        self.assertEqual(self.llm.complete.await_count, 5)
        self.assertIn('用户本次提问：不用查了', self.last_text())

    async def test_users_remain_independent_and_stop_is_scoped(self):
        self.send(1, '甲的问题')
        await self.pending()
        self.send(2, '乙的问题', user_id=999)
        await asyncio.sleep(0)
        await self.bot.handle(event(3, '不用查了'))
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_awaited_once()
        self.assertEqual(self.last_text(), '乙的问题')
        self.assertEqual(len(self.db.history('private:456:999', 20)), 1)

    async def test_maximum_wait_flushes_even_while_messages_keep_arriving(self):
        self.bot.cfg = replace(self.bot.cfg, message_merge_seconds=.08, message_merge_max_seconds=.1)
        first_called = asyncio.Event()
        async def answer(*args):
            first_called.set()
            return '完成'
        self.llm.complete.side_effect = answer
        start = time.monotonic()
        self.send(1, '第1句')
        await self.pending()
        for number in range(2, 5):
            await asyncio.sleep(.025)
            self.send(number, f'第{number}句')
        await asyncio.wait_for(first_called.wait(), .1)
        self.assertLess(time.monotonic() - start, .19)
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_awaited_once()

    async def test_commands_flush_and_model_switch_cancels_buffer(self):
        self.send(1, '不应在切换后回答')
        await self.pending()
        await self.bot.handle(event(2, '/model second'))
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_not_awaited()
        self.assertEqual(self.db.model('private:456:123', 'first'), 'second')
        await self.bot.handle(event(3, '/status'))
        self.assertIn('支持自然打断与更正', self.transport.send.call_args.args[1])

    async def test_merge_size_bound_keeps_order_and_each_fragment_once(self):
        self.bot.cfg = replace(self.bot.cfg, max_input_chars=6)
        self.send(1, '第一句')
        await self.pending()
        self.send(2, '第二句')
        await asyncio.gather(*self.tasks)
        self.assertEqual([c.args[1][-1]['content'] for c in self.llm.complete.call_args_list],
                         ['第一句', '第二句'])

    async def test_empty_input_and_friend_greeting_do_not_pollute_burst(self):
        await self.bot.handle(event(1, '我们已经是好友啦，一起来聊天吧'))
        self.send(2, '真正的问题')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.last_text(), '真正的问题')

    async def test_actual_agent_tool_loop_receives_merged_question(self):
        model = replace(self.bot.cfg.models['first'], supports_tools=True)
        self.bot.cfg = replace(self.bot.cfg, models={'first': model})
        self.llm.turn.side_effect = [ModelReply('', (ToolCall('a', 'calculate', '{"expression":"17+29"}'),)),
                                    ModelReply('46')]
        self.send(1, '请调用计算工具')
        await self.pending()
        self.send(2, '计算17加29')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.turn.await_count, 2)
        first = self.llm.turn.call_args_list[0].args[1]
        self.assertEqual(first[-1]['content'], '请调用计算工具\n计算17加29')
        self.assertEqual(self.transport.send.call_args.args[1], '46')

    async def test_correction_preserves_forced_search(self):
        self.bot.cfg = replace(self.bot.cfg, auto_search=False)
        search = AsyncMock()
        started = asyncio.Event()
        async def query(text):
            if search.query.await_count == 1:
                started.set()
                await asyncio.Event().wait()
            return [{'title': '资料', 'url': 'https://example.test', 'content': '测试资料'}]
        search.query.side_effect = query
        self.bot.search = search
        self.send(1, '/search 查北京天气')
        await started.wait()
        self.send(2, '我刚才说错了，是上海')
        await asyncio.gather(*self.tasks)
        self.assertEqual(search.query.await_count, 2)
        self.assertIn('上海', search.query.call_args.args[0])

    async def test_correction_targets_latest_queued_question(self):
        started = asyncio.Event()
        async def model(*args):
            if self.llm.complete.await_count == 1:
                started.set()
                await asyncio.Event().wait()
            return '改后的答案'
        self.llm.complete.side_effect = model
        self.send(1, '旧的独立问题')
        await started.wait()
        self.send(2, '另一个问题，北京的气候')
        await self.pending()
        self.send(3, '我刚才说错了，是上海')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 2)
        self.assertNotIn('旧的独立问题', self.last_text())
        self.assertIn('另一个问题', self.last_text())
        self.assertIn('上海', self.last_text())
        self.assertEqual(self.db.source_ids('private:456:123'), {'2', '3'})

    async def test_recalling_carried_image_cancels_replacement_burst(self):
        started = asyncio.Event()
        async def model(*args):
            started.set()
            await asyncio.Event().wait()
        self.llm.complete.side_effect = model
        self.send(1, message=[picture(), {'type': 'text', 'data': {'text': '看右侧'}}])
        await started.wait()
        self.send(2, '更正一下，看左侧')
        await self.pending()
        await self.bot.handle(recalled(1))
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 1)
        self.transport.send.assert_not_awaited()
        self.assertFalse(self.bot.jobs)

    async def test_reset_cancels_buffer_without_erasing_other_user_history(self):
        self.db.append('private:456:999', '别人的历史', '保留', 20)
        self.db.append('private:456:123', '我的历史', '清理', 20)
        self.send(1, '还没开始的问题')
        await self.pending()
        await self.bot.handle(event(2, '/reset'))
        await asyncio.gather(*self.tasks)
        self.llm.complete.assert_not_awaited()
        self.assertEqual(self.db.history('private:456:123', 20), [])
        self.assertEqual(self.db.history('private:456:999', 20), [('别人的历史', '保留')])

    async def test_two_references_stay_separate_and_unsupported_files_do_not_join(self):
        for number in (40, 41):
            self.bot.references.remember('private:456:123', number,
                                         [{'type': 'text', 'data': {'text': f'原文{number}'}}])
        self.send(1, message=[{'type': 'reply', 'data': {'id': '40'}},
                              {'type': 'text', 'data': {'text': '解释原文40'}}])
        await self.pending()
        self.send(2, message=[{'type': 'reply', 'data': {'id': '41'}},
                              {'type': 'text', 'data': {'text': '解释原文41'}}])
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 2)
        self.assertIn('原文41', self.last_text())
        self.assertNotIn('原文40', self.last_text())

    async def test_idle_correction_is_an_ordinary_followup(self):
        await self.bot.handle(event(1, '刚才说错了，是上海'))
        self.assertEqual(self.last_text(), '刚才说错了，是上海')

    async def test_fragment_count_cap_flushes_before_thirteenth_message(self):
        for number in range(1, 14):
            self.send(number, str(number))
            await asyncio.sleep(0)
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.llm.complete.await_count, 2)
        texts = [c.args[1][-1]['content'] for c in self.llm.complete.call_args_list]
        self.assertEqual(texts, ['\n'.join(map(str, range(1, 13))), '13'])


class ControlAndConfigTests(unittest.TestCase):
    def test_control_boundaries(self):
        for text in ('不用查了', '不用查了。', '先停一下！', '别回答了，谢谢'):
            self.assertEqual(control(text), ('stop', ''))
        for text in ('他说不用查了', '不用查了是什么意思', '不要取消', '取消政策是什么',
                     '```不用查了```', '“我刚才说错了”这句话'):
            self.assertIsNone(control(text)[0])
        self.assertIsNone(plain_text([{'type': 'reply', 'data': {'id': '1'}},
                                     {'type': 'text', 'data': {'text': '不用查了'}}]))

    def test_configuration_rejects_unbounded_or_invalid_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            base = 'default_model="m"\n[models.m]\nbase_url="https://example.test/v1"\nmodel="m"\napi_key_env="TEST_KEY"\n'
            with patch.dict(os.environ, {'CONFIG_PATH': str(path), 'OWNER_QQ': '123',
                                       'ONEBOT_TOKEN': 'test', 'TEST_KEY': 'key'}):
                for value in ('message_merge_seconds=-1', 'message_merge_seconds=nan',
                              'message_merge_seconds=true', 'message_merge_seconds=6',
                              'message_merge_max_seconds=11', 'message_merge_max_seconds=0.5'):
                    path.write_text(value + '\n' + base)
                    with self.assertRaises(ValueError):
                        load()
                path.write_text('message_merge_seconds=0\n' + base)
                self.assertEqual(load().message_merge_seconds, 0)


class BurstNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_to_real_http_model_merges_and_sends_once(self):
        app, requests, replies = web.Application(), [], []
        delivered = asyncio.Event()
        async def model(request):
            data = await request.json()
            requests.append(data)
            return web.json_response({'choices': [{'message': {'content': '合并回答'}}]})
        async def qq(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            for n, text in enumerate(('帮我看看', '这个问题', '要详细步骤'), 1):
                await ws.send_json(event(n, text))
            async for frame in ws:
                action = frame.json()
                replies.append(action['params']['message'][0]['data']['text'])
                await ws.send_json({'echo': action['echo'], 'status': 'ok', 'retcode': 0,
                                    'data': {'message_id': 10}})
                delivered.set()
            return ws
        app.router.add_post('/v1/chat/completions', model)
        app.router.add_get('/ws', qq)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        db = Store(':memory:')
        with patch.dict(os.environ, {'TEST_KEY': 'test-key'}):
            async with aiohttp.ClientSession() as http:
                cfg = replace(config(), message_merge_seconds=.02,
                              models={'first': Model(url + '/v1', 'm', 'TEST_KEY')})
                transport = OneBot(http, url.replace('http://', 'ws://') + '/ws', cfg.token)
                bot = Bot(cfg, db, LLM(http, cfg), transport)
                tasks = [asyncio.create_task(transport.run(bot.accepts, bot.recall)),
                         asyncio.create_task(bot.worker())]
                try:
                    await asyncio.wait_for(delivered.wait(), 2)
                    await asyncio.wait_for(transport.queue.join(), 2)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['messages'][-1]['content'], '帮我看看\n这个问题\n要详细步骤')
                    self.assertEqual(replies, ['合并回答'])
                    self.assertEqual(len(db.history('private:456:123', 20)), 1)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await bot.close()
        db.close()
        await runner.cleanup()
