import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from qqbot.bot import Bot
from qqbot.llm import ModelError
from qqbot.search import Search, SearchUnavailable, needs_search, search_plan, search_context, parse_results
from qqbot.store import Store
from test_bot import config, event
from test_images import picture


def recall(number=1, user_id=123, **kwargs):
    return {'post_type': 'notice', 'notice_type': 'friend_recall', 'self_id': 456,
            'user_id': user_id, 'message_id': number, **kwargs}


class RecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.llm, self.transport, self.search, self.images = (AsyncMock() for _ in range(4))
        self.llm.complete.return_value = '回答'
        self.images.load.return_value = ['data:image/png;base64,x']
        self.bot = Bot(replace(config(), image_wait_seconds=.02, progress_delay_seconds=.2),
                       self.store, self.llm, self.transport, self.images, self.search)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_salary_answer_uses_system_level_evidence_rules(self):
        self.search.query.return_value = [{'url': 'https://example.test', 'title': '教师招聘', 'content': '年薪15万元'}]
        await self.bot.handle(event(1, '岳阳市教师薪资'))
        conversation = self.llm.complete.call_args.args[1]
        self.assertIn('不能凭常识补出', conversation[0]['content'])
        self.assertIn('必须注明样本年份', conversation[0]['content'])
        self.assertIn('年薪15万元', conversation[-1]['content'])

    async def test_recall_stops_model_without_reply_history_or_quote_cache(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        self.llm.complete.side_effect = slow
        task = asyncio.create_task(self.bot.handle(event(1, '待撤回的秘密')))
        await started.wait()
        await self.bot.handle(recall())
        await asyncio.wait_for(task, 1)
        self.assertTrue(stopped.is_set())
        self.transport.send.assert_not_awaited()
        self.assertEqual(self.store.history('private:456:123', 20), [])
        self.assertFalse(self.bot.references.cache)
        self.assertFalse(self.bot.jobs)
        await self.bot.handle(event(1, '待撤回的秘密'))
        self.assertEqual(self.llm.complete.await_count, 1)

    async def test_recall_queued_message_does_not_cancel_other_work(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            started.set()
            await release.wait()
            return '第一题答案'
        self.llm.complete.side_effect = slow
        active = asyncio.create_task(self.bot.handle(event(1, '第一题')))
        await started.wait()
        queued = asyncio.create_task(self.bot.handle(event(2, '第二题')))
        await asyncio.sleep(0)
        await self.bot.handle(recall(2))
        release.set()
        await asyncio.gather(active, queued)
        self.llm.complete.assert_awaited_once()
        self.assertEqual(self.store.history('private:456:123', 20), [('第一题', '第一题答案')])

    async def test_recall_before_dispatch_and_other_users_isolation(self):
        await self.bot.handle(recall(1))
        await self.bot.handle(event(1))
        await self.bot.handle(event(1, user_id=999))
        self.llm.complete.assert_awaited_once()
        self.assertEqual(self.transport.send.call_args.args[0], 999)
        for invalid in [recall(2, notice_type='group_recall'), recall(2, user_id=456), recall(2, user_id=True)]:
            await self.bot.handle(invalid)
        self.assertFalse(self.store.is_recalled('private:456:123', 2))

    async def test_pending_picture_recall_prevents_prompt(self):
        await self.bot.handle(event(1, message=[picture()]))
        await self.bot.handle(recall(1))
        await asyncio.sleep(.04)
        self.assertFalse(self.bot.pending_images)
        self.transport.send.assert_not_awaited()
        self.llm.complete.assert_not_awaited()

    async def test_recalled_picture_cancels_followup_that_consumed_it(self):
        started = asyncio.Event()
        async def slow(*args):
            started.set()
            await asyncio.Event().wait()
        self.llm.complete.side_effect = slow
        await self.bot.handle(event(1, message=[picture()]))
        task = asyncio.create_task(self.bot.handle(event(2, '第一题')))
        await started.wait()
        await self.bot.handle(recall(1))
        await task
        self.assertEqual(self.store.history('private:456:123', 20), [])
        self.transport.send.assert_not_awaited()

    async def test_recall_during_quote_lookup_and_search(self):
        for is_search in [False, True]:
            started = asyncio.Event()
            async def slow(*args):
                started.set()
                await asyncio.Event().wait()
            number = 20 if is_search else 10
            if is_search:
                self.search.query.side_effect = slow
                message = event(number, '/search 岳阳市教师薪资')
            else:
                self.transport.get_message.side_effect = slow
                message = event(number, message=[{'type': 'reply', 'data': {'id': '7'}},
                                                {'type': 'text', 'data': {'text': '解释'}}])
            task = asyncio.create_task(self.bot.handle(message))
            await started.wait()
            await self.bot.handle(recall(number))
            await asyncio.wait_for(task, 1)
        self.llm.complete.assert_not_awaited()
        self.transport.send.assert_not_awaited()

    async def test_completed_turn_deleted_and_recalled_quote_cannot_be_fetched(self):
        await self.bot.handle(event(1, '隐私文字'))
        await self.bot.handle(recall(1))
        self.assertEqual(self.store.history('private:456:123', 20), [])
        await self.bot.handle(event(2, message=[{'type': 'reply', 'data': {'id': '1'}},
                                              {'type': 'text', 'data': {'text': '引用'}}]))
        self.transport.get_message.assert_not_awaited()
        self.assertEqual(self.llm.complete.await_count, 1)

    async def test_recall_during_delivery_prevents_remaining_chunks_and_history(self):
        sending = asyncio.Event()
        async def send(*args):
            sending.set()
            await asyncio.Event().wait()
        self.transport.send.side_effect = send
        self.llm.complete.return_value = '长回答' * 20
        self.bot.cfg = replace(self.bot.cfg, reply_chunk_chars=10)
        task = asyncio.create_task(self.bot.handle(event(1, '问题')))
        await sending.wait()
        await self.bot.handle(recall(1))
        await asyncio.wait_for(task, 1)
        self.transport.send.assert_awaited_once()
        self.assertEqual(self.store.history('private:456:123', 20), [])

    def test_recall_marker_survives_restart_and_does_not_store_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + '/chat.db')
            store.append('s', '秘密', '回答', 20, {'5'})
            store.recall('s', '5')
            store.close()
            store = Store(directory + '/chat.db')
            self.assertTrue(store.is_recalled('s', '5'))
            self.assertEqual(store.history('s', 20), [])
            self.assertEqual(store.source_ids('s'), set())
            store.close()


class DeepSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_salary_noun_phrases_trigger_and_queries_cover_different_evidence(self):
        teacher = '岳阳市教师薪资'
        graduate = '能源与动力工程专业华中科技大学薪资水平'
        self.assertTrue(needs_search(teacher))
        self.assertTrue(needs_search(graduate))
        self.assertFalse(needs_search('我刚发工资了'))
        self.assertEqual(len(search_plan(teacher)), 3)
        self.assertIn('编', search_plan(teacher)[2])
        self.assertIn('就业质量报告', search_plan(graduate)[1])
        self.assertTrue(search_plan(graduate)[2].startswith('能源与动力工程 应届'))
        self.assertEqual(search_plan('今天天气'), ['今天天气'])

    def test_salary_excerpts_preserve_late_pay_and_skip_garbled_pages(self):
        data = {'results': [
            {'title': 'ڶڽʦڶڽʦڶڽʦ', 'url': 'https://bad.test', 'content': '工资5000元'},
            {'title': '教师招聘', 'url': 'https://school.test',
             'content': '栏目导航 ' * 800 + '岳阳高中合同制教师，税前年薪12万至15万元，不含公积金。'}]}
        results = parse_results(data, salary=True)
        self.assertEqual(len(results), 1)
        self.assertIn('税前年薪12万至15万元', results[0]['content'])
        self.assertIn('合同制', results[0]['content'])

    async def test_multiple_queries_deduplicate_interleave_and_keep_partial_results(self):
        search = Search(config())
        a = {'url': 'https://a.test', 'title': '甲', 'content': '月薪6000元'}
        b = {'url': 'https://b.test', 'title': '乙', 'content': '税前年薪12万元'}
        search.fetch = AsyncMock(side_effect=[[a], ModelError('一次失败'), [a, b]])
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test'}):
            results = await search.query('岳阳市教师薪资')
        self.assertEqual(search.fetch.await_count, 3)
        self.assertTrue(all(call.args[3] for call in search.fetch.call_args_list))
        self.assertEqual([r['url'] for r in results], ['https://a.test', 'https://b.test'])
        self.assertTrue(results[0]['partial'])
        context = search_context(results, '教师薪资')
        self.assertIn('参考区间', context)
        self.assertIn('税前/到手', context)
        self.assertIn('部分检索失败', context)

    async def test_auth_failure_stops_further_queries(self):
        search = Search(config())
        search.fetch = AsyncMock(side_effect=SearchUnavailable('HTTP 401'))
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test'}):
            with self.assertRaisesRegex(ModelError, '401'):
                await search.query('教师工资')
        search.fetch.assert_awaited_once()

    async def test_query_budget_and_context_budget(self):
        search = Search(replace(config(), search_max_queries=1))
        search.fetch = AsyncMock(return_value=[{'url': 'https://a.test', 'title': '甲', 'content': '薪资' * 10000}])
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test'}):
            results = await search.query('教师工资')
        search.fetch.assert_awaited_once()
        self.assertLessEqual(len(search_context(results, '教师工资', 5000).encode()), 5000)
