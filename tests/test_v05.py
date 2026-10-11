import asyncio
import os
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from qqbot.bot import Bot
from qqbot.llm import ModelError
from qqbot.onebot import OneBot
from qqbot.search import Search, needs_search, parse_results, strip_search_citations
from qqbot.store import Store
from test_bot import config, event
from test_images import picture, IMAGE_URL


def quote(ref, text='分析一下'):
    return [{'type': 'reply', 'data': {'id': str(ref)}},
            {'type': 'text', 'data': {'text': text}}]


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.llm = AsyncMock()
        self.llm.complete.return_value = '简短回答'
        self.transport = SimpleNamespace(send=AsyncMock(return_value=900), get_message=AsyncMock())
        self.loader, self.search = AsyncMock(), AsyncMock()
        self.loader.load.return_value = ['data:image/png;base64,dGVzdA==']
        self.cfg = replace(config(), image_wait_seconds=.03, media_ttl_seconds=.3, progress_delay_seconds=.03)
        self.bot = Bot(self.cfg, self.store, self.llm, self.transport, self.loader, self.search)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_image_text_before_deadline_has_no_prompt(self):
        await self.bot.handle(event(message=[picture()]))
        await self.bot.handle(event(1, message=[picture()]))  # duplicate
        self.llm.complete.assert_not_awaited()
        await self.bot.handle(event(2, '只读第一题'))
        await asyncio.sleep(.05)
        self.assertEqual(self.transport.send.await_count, 1)
        content = self.llm.complete.call_args.args[1][-1]['content']
        self.assertEqual(content[0]['text'], '只读第一题')
        self.loader.load.assert_awaited_once_with([IMAGE_URL])
        self.assertFalse(self.bot.pending_images)

    async def test_late_text_uses_retained_image_and_no_automatic_ocr(self):
        await self.bot.handle(event(message=[picture()]))
        await asyncio.sleep(.05)
        self.llm.complete.assert_not_awaited()
        self.assertEqual(self.transport.send.call_args.args[1], '收到图片了，你想让我看什么？')
        await self.bot.handle(event(2, '解第 2 题'))
        self.loader.load.assert_awaited_once()
        self.assertEqual(self.llm.complete.await_count, 1)

    async def test_pending_images_isolated_and_reset_cancels_prompt(self):
        await self.bot.handle(event(message=[picture()], user_id=999))
        await self.bot.handle(event(2, '你好', user_id=888))
        self.loader.load.assert_not_awaited()
        await self.bot.handle(event(2, '/reset', user_id=999))
        await asyncio.sleep(.05)
        self.assertFalse(self.bot.pending_images)
        self.assertEqual(self.transport.send.await_count, 2)

    async def test_image_expiry_and_multiple_batch(self):
        await self.bot.handle(event(message=[picture()]))
        await self.bot.handle(event(2, message=[picture('https://qpic.cn/b')]))
        await self.bot.handle(event(3, '比较两张图片'))
        self.assertEqual(len(self.loader.load.call_args.args[0]), 2)
        await self.bot.handle(event(4, message=[picture()]))
        await asyncio.sleep(.34)
        await self.bot.handle(event(5, '新话题'))
        self.assertEqual(self.loader.load.await_count, 1)

    async def test_friend_notifications_and_requests_are_silent(self):
        for i, text in enumerate(['请求添加你为好友', '我们已成功添加为好友，现在可以开始聊天啦～']):
            await self.bot.handle(event(i, text))
        await self.bot.handle(event(3, post_type='request'))
        self.transport.send.assert_not_awaited()
        await self.bot.handle(event(4, '你好呀'))
        self.llm.complete.assert_awaited_once()

    async def test_quote_cached_user_bot_and_picture(self):
        await self.bot.handle(event(1, '引用测试'))
        await self.bot.handle(event(2, message=quote(1, '换个说法')))
        self.assertIn('引用测试', self.llm.complete.call_args.args[1][-1]['content'])
        await self.bot.handle(event(3, message=quote(900, '你刚才什么意思')))
        self.assertIn('简短回答', self.llm.complete.call_args.args[1][-1]['content'])
        await self.bot.handle(event(4, message=[picture()]))
        await self.bot.handle(event(5, message=quote(4, '只看第一题')))
        self.loader.load.assert_awaited_once_with([IMAGE_URL])
        self.transport.get_message.assert_not_awaited()

    async def test_uncached_quote_authorization_and_failure(self):
        for data in [None, {'message_type': 'group', 'sender': {'user_id': 123}},
                     {'message_type': 'private', 'sender': {'user_id': 999}},
                     {'message_type': 'private', 'sender': {'user_id': 456}}]:
            self.transport.get_message.return_value = data
            await self.bot.handle(event(str(data), message=quote(100)))
        self.llm.complete.assert_not_awaited()
        self.transport.get_message.return_value = {'message_type': 'private', 'sender': {'user_id': 123},
            'message': [{'type': 'text', 'data': {'text': '此前文字'}}]}
        await self.bot.handle(event(10, message=quote(100)))
        self.assertIn('此前文字', self.llm.complete.call_args.args[1][-1]['content'])

    async def test_search_sources_and_failure_not_saved(self):
        self.search.query.return_value = [{'title': '来源', 'url': 'https://example.com/a', 'content': '证据'}]
        self.llm.complete.return_value = '今天有雨[1]，记得带伞【1】。'
        await self.bot.handle(event(1, '/search 上海天气'))
        self.search.query.assert_awaited_once_with('上海天气')
        self.assertIn('本次联网', self.llm.complete.call_args.args[1][-1]['content'])
        self.assertEqual(self.transport.send.call_args.args[1], '今天有雨，记得带伞。')
        self.assertIn('https://example.com/a', self.llm.complete.call_args.args[1][-1]['content'])
        self.assertEqual(self.store.history('private:456:123', 20)[0][1], '今天有雨，记得带伞。')
        self.search.query.side_effect = ModelError('搜索失败')
        await self.bot.handle(event(2, '今天的天气'))
        self.assertEqual(len(self.store.history('private:456:123', 20)), 1)
        self.assertEqual(self.llm.complete.await_count, 1)
        self.assertIn('搜索失败', self.transport.send.call_args.args[1])

    async def test_cancel_active_and_queued_requests(self):
        started = asyncio.Event()
        async def slow(*args):
            started.set()
            await asyncio.Event().wait()
        self.llm.complete.side_effect = slow
        active = asyncio.create_task(self.bot.handle(event(1, '慢问题')))
        await started.wait()
        queued = asyncio.create_task(self.bot.handle(event(2, '排队问题')))
        await asyncio.sleep(.05)
        self.assertEqual(self.transport.send.call_args.args[1], '稍等稍等，我还在找呢，可以输入/cancel取消哦')
        await self.bot.handle(event(3, '/cancel'))
        await asyncio.wait_for(asyncio.gather(active, queued), 1)
        self.assertEqual(self.llm.complete.await_count, 1)
        self.assertEqual(self.store.history('private:456:123', 20), [])
        self.llm.complete.side_effect = None
        await self.bot.handle(event(4, '新问题'))
        self.assertEqual(self.llm.complete.await_count, 2)

    async def test_cancel_discards_older_transport_queue_event(self):
        arrival = time.monotonic()
        await self.bot.handle(event(1, '/cancel'))
        await self.bot.handle(event(2, '旧队列问题', _received_at=arrival))
        self.llm.complete.assert_not_awaited()

    async def test_explicit_image_quote_does_not_merge_unrelated_pending_image(self):
        self.bot.references.remember('private:456:123', 50, [picture('https://qpic.cn/quoted')])
        await self.bot.handle(event(1, message=[picture()]))
        await self.bot.handle(event(2, message=quote(50, '解释这张')))
        self.loader.load.assert_awaited_once_with(['https://qpic.cn/quoted'])

    async def test_cancel_while_resolving_quote_does_not_start_model(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            started.set()
            await release.wait()
            return {'message_type': 'private', 'sender': {'user_id': 123},
                    'message': [{'type': 'text', 'data': {'text': '引用'}}]}
        self.transport.get_message.side_effect = fetch
        task = asyncio.create_task(self.bot.handle(event(1, message=quote(55))))
        await started.wait()
        await self.bot.handle(event(2, '/cancel'))
        release.set()
        await task
        self.llm.complete.assert_not_awaited()

    async def test_reset_during_generation_clears_old_context(self):
        started = asyncio.Event()
        async def slow(*args):
            started.set()
            await asyncio.Event().wait()
        self.store.append('private:456:123', '旧问题', '旧回答', 20)
        self.llm.complete.side_effect = slow
        active = asyncio.create_task(self.bot.handle(event(1)))
        await started.wait()
        await self.bot.handle(event(2, '/reset'))
        await active
        self.assertEqual(self.store.history('private:456:123', 20), [])


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def test_search_citations_preserve_code_and_non_source_numbers(self):
        results = [{'url': 'https://example.com/a'}, {'url': 'https://example.com/b'}]
        text = '结论[1, 2]。依据[1](https://example.com/a)。`a[1]`\n```python\na = [1]\n```\na[1] 与 [99]'
        expected = '结论。依据。`a[1]`\n```python\na = [1]\n```\na[1] 与 [99]'
        self.assertEqual(strip_search_citations(text, results), expected)

    async def test_no_key_and_bad_results(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': ''}):
            with self.assertRaisesRegex(ModelError, '未配置'):
                await Search(config()).query('天气')
        for data in [None, {}, {'results': []}, {'results': [{'url': 'file:///etc/passwd', 'content': 'x'}]}]:
            with self.assertRaises(ModelError):
                parse_results(data)
        self.assertTrue(needs_search('三大运营商套餐资费怎么样？'))
        self.assertFalse(needs_search('如何学习 Python'))
        self.assertFalse(needs_search('今天心情不好'))
        self.assertFalse(needs_search('我刚发工资了'))

    async def test_search_http_errors_and_payload(self):
        response = AsyncMock()
        response.status = 200
        response.json.return_value = {'results': [{'title': '文档', 'url': 'https://example.com', 'content': '正文'}]}
        http = MagicMock()
        http.post.return_value.__aenter__ = AsyncMock(return_value=response)
        session = MagicMock()
        session.return_value.__aenter__ = AsyncMock(return_value=http)
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'test-key'}), patch('qqbot.search.aiohttp.ClientSession', session):
            self.assertEqual(len(await Search(config()).query('问题')), 1)
            self.assertEqual(http.post.call_args.kwargs['json']['query'], '问题')
            self.assertEqual(http.post.call_args.kwargs['headers']['Authorization'], 'Bearer test-key')
            for status in [401, 429, 500]:
                response.status = status
                with self.assertRaisesRegex(ModelError, str(status)):
                    await Search(config()).query('问题')
            http.post.side_effect = TimeoutError()
            with self.assertRaisesRegex(ModelError, '搜索失败'):
                await Search(config()).query('问题')

    async def test_onebot_status_and_generic_get_message_action(self):
        transport = OneBot(None, 'ws://test', 'secret')
        transport.update_status({'online': False})
        self.assertIs(transport.online, False)
        transport.update_status({'online': True})
        self.assertIs(transport.online, True)
        async def send_json(payload):
            self.assertEqual(payload['action'], 'get_msg')
            self.assertEqual(payload['params'], {'message_id': '123'})
            transport.pending[payload['echo']].set_result({'status': 'ok', 'retcode': 0, 'data': {'message': []}})
        transport.ws = SimpleNamespace(closed=False, send_json=send_json)
        self.assertEqual(await transport.get_message('123'), {'message': []})
