import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from qqbot.bot import Bot
from qqbot.progress import DEFAULT_TEMPLATES, ProgressState, ProgressWording, task_kind
from qqbot.store import Store
from test_bot import config


class WordingTests(unittest.TestCase):
    def test_task_classification_and_generic_fallback(self):
        for query, image, expected in [('解这道题', True, 'reasoning'), ('证明这个公式', False, 'reasoning'),
                ('写一篇文章', False, 'writing'), ('帮我调试 Python 代码', False, 'coding'),
                ('看一下', True, 'image'), ('你好', False, 'thinking')]:
            self.assertEqual(task_kind(query, image), expected)

    def test_custom_phrases_and_no_consecutive_repetition(self):
        wording = ProgressWording({'reasoning': ['让我想想哦', '我再理理思路哦']})
        first = wording.choose('friend', 'reasoning')
        second = wording.choose('friend', 'reasoning')
        self.assertNotEqual(first, second)
        self.assertIn(second, ['让我想想哦，可以输入/cancel取消哦', '我再理理思路哦，可以输入/cancel取消哦'])
        self.assertIn(wording.choose('friend', 'search').split('，可以输入')[0], DEFAULT_TEMPLATES['search'])


class StageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.llm, self.transport, self.search, self.images = (AsyncMock() for _ in range(4))
        self.llm.complete.return_value = '答案'
        self.images.load.return_value = ['data:image/png;base64,x']
        self.bot = Bot(replace(config(), progress_delay_seconds=.02), self.store,
                       self.llm, self.transport, self.images, self.search)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_stage_tracks_queue_search_image_and_reasoning(self):
        state = ProgressState()
        async def search(query):
            self.assertEqual(state.stage, 'search')
            return [{'url': 'https://example.com', 'title': '题目', 'content': '资料'}]
        async def images(urls):
            self.assertEqual(state.stage, 'image')
            return ['data:image/png;base64,x']
        async def complete(*args):
            self.assertEqual(state.stage, 'reasoning')
            return '答案'
        self.search.query.side_effect = search
        self.images.load.side_effect = images
        self.llm.complete.side_effect = complete
        self.bot.model_slots = asyncio.Semaphore(0)
        task = asyncio.create_task(self.bot.generate('s', 'first', '解题', ['url'], '解题', True, state))
        await asyncio.sleep(0)
        self.assertEqual(state.stage, 'queued')
        self.bot.model_slots.release()
        self.assertEqual(await task, '答案')

    async def test_search_finished_uses_summary_not_search_wording(self):
        state = ProgressState()
        self.search.query.return_value = [{'url': 'https://example.com', 'title': '资料', 'content': '信息'}]
        await self.bot.generate('s', 'first', '查个信息', [], '查个信息', True, state)
        self.assertEqual(state.stage, 'summary')

    async def test_prompt_uses_stage_at_notification_time(self):
        state = ProgressState()
        task = asyncio.create_task(self.bot.progress(123, 's', state))
        await asyncio.sleep(0)
        state.stage = 'writing'
        await task
        self.assertIn(self.transport.send.call_args.args[1],
                      [text + '，可以输入/cancel取消哦' for text in DEFAULT_TEMPLATES['writing']])
        self.transport.send.assert_awaited_once()
