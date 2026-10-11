import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from qqbot.bot import Bot
from qqbot.llm import ModelError
from qqbot.onebot import DeliveryError
from qqbot.store import Store
from test_bot import config, event
from test_images import PNG, picture
from qqbot.images import image_data_url


class FriendsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.llm = AsyncMock()
        self.llm.complete.return_value = '回答'
        self.transport = SimpleNamespace(send=AsyncMock(), queue=asyncio.Queue())
        self.bot = Bot(config(), self.store, self.llm, self.transport)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_all_friends_routed_and_history_isolated(self):
        # Same message id from two people must not collide; numeric strings work.
        await self.bot.handle(event(1, '主人秘密'))
        await self.bot.handle(event(1, '好友一', user_id='999'))
        await self.bot.handle(event(1, '好友二', user_id=888))
        await self.bot.handle(event(2, '继续', user_id=999))
        self.assertEqual([c.args[0] for c in self.transport.send.call_args_list], [123, 999, 888, 999])
        conversation = self.llm.complete.call_args.args[1]
        self.assertEqual([m['content'] for m in conversation[1:]], ['好友一', '回答', '继续'])
        self.assertEqual(self.store.history('private:456:123', 20), [('主人秘密', '回答')])
        await self.bot.handle(event(1, '好友一', user_id=999))
        self.assertEqual(self.llm.complete.await_count, 4)

    async def test_only_friend_messages_accepted(self):
        for extra in ({'sub_type': 'other'}, {'sub_type': 'group'}, {'sub_type': None},
                      {'message_type': 'group'}, {'post_type': 'request'},
                      {'post_type': 'message_sent'}, {'user_id': 456}, {'user_id': True},
                      {'user_id': 0}, {'user_id': -1}, {'user_id': 'invalid'},
                      {'self_id': None}, {'message_id': False}):
            await self.bot.handle(event(**extra))
        self.llm.complete.assert_not_awaited()
        self.transport.send.assert_not_awaited()

    async def test_permissions_reset_and_help(self):
        await self.bot.handle(event(1, 'owner'))
        await self.bot.handle(event(1, 'friend', user_id=999))
        for n, command in enumerate(['/models', '/model second'], 2):
            await self.bot.handle(event(n, command, user_id=999))
            self.assertIn('仅主人', self.transport.send.call_args.args[1])
        self.assertEqual(self.store.model('private:456:999', 'first'), 'first')
        await self.bot.handle(event(4, '/help', user_id=999))
        self.assertNotIn('/model', self.transport.send.call_args.args[1])
        await self.bot.handle(event(5, '/status', user_id=999))
        self.assertIn('1 轮', self.transport.send.call_args.args[1])
        self.assertNotIn('当前模型', self.transport.send.call_args.args[1])
        await self.bot.handle(event(6, '/reset', user_id=999))
        self.assertEqual(self.store.history('private:456:999', 20), [])
        self.assertEqual(len(self.store.history('private:456:123', 20)), 1)
        await self.bot.handle(event(2, '/model second'))
        self.assertEqual(self.store.model('private:456:123', 'first'), 'second')
        self.assertEqual(self.store.model('private:456:999', 'first'), 'first')

    async def test_old_owner_history_and_new_default(self):
        self.store.reset('private:456:123', 'second')
        self.store.append('private:456:123', '旧消息', '旧回复', 20)
        await self.bot.handle(event(2, '继续'))
        self.assertEqual(self.llm.complete.call_args.args[0].model, 'm2')
        self.assertEqual(self.llm.complete.call_args.args[1][1]['content'], '旧消息')
        await self.bot.handle(event(1, 'friend', user_id=999))
        self.bot.cfg = replace(self.bot.cfg, default_model='second')
        await self.bot.handle(event(2, 'new model', user_id=999))
        self.assertEqual(self.llm.complete.call_args.args[0].model, 'm2')
        self.assertEqual(len(self.llm.complete.call_args.args[1]), 2)
        self.assertEqual(len(self.store.history('private:456:123', 20)), 2)

    async def test_rate_limit_is_per_user_and_commands_remain_available(self):
        self.bot.cfg = replace(self.bot.cfg, requests_per_minute=1)
        clock = SimpleNamespace(monotonic=lambda: 100)
        with patch('qqbot.bot.time', clock):
            await self.bot.handle(event(1, user_id=999))
            await self.bot.handle(event(2, user_id=999))
            self.assertIn('过于频繁', self.transport.send.call_args.args[1])
            self.assertEqual(self.llm.complete.await_count, 1)
            await self.bot.handle(event(1, user_id=888))
            self.assertEqual(self.llm.complete.await_count, 2)
            await self.bot.handle(event(3, '/reset', user_id=999))
            await self.bot.handle(event(4, user_id=999))
            self.assertEqual(self.llm.complete.await_count, 2)
            clock.monotonic = lambda: 161
            await self.bot.handle(event(5, user_id=999))
            self.assertEqual(self.llm.complete.await_count, 3)

    async def test_friend_image_and_errors_reply_to_sender(self):
        loader = AsyncMock()
        loader.load.return_value = [image_data_url(PNG)]
        self.bot.image_loader = loader
        await self.bot.handle(event(message=[picture(), {"type": "text", "data": {"text": "看图"}}], user_id=999))
        self.assertEqual(self.transport.send.call_args.args[0], 999)
        self.assertIsInstance(self.llm.complete.call_args.args[1][-1]['content'], list)
        self.llm.complete.side_effect = ModelError('模型超时')
        await self.bot.handle(event(2, user_id=999))
        self.assertEqual(self.transport.send.call_args.args[0], 999)
        self.assertIn('模型超时', self.transport.send.call_args.args[1])
        self.assertEqual(len(self.store.history('private:456:999', 20)), 1)
        self.assertEqual(self.store.history('private:456:123', 20), [])

    async def test_worker_allows_other_user_while_preserving_same_user_order(self):
        started, other_done, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        seen = []
        async def complete(model, conversation):
            text = conversation[-1]['content']
            seen.append(text)
            if text == 'A1':
                started.set()
                await release.wait()
            if text == 'A2':
                self.assertEqual(conversation[1]['content'], 'A1')
            if text == 'B1':
                other_done.set()
            return text
        self.llm.complete.side_effect = complete
        worker = asyncio.create_task(self.bot.worker())
        try:
            await self.transport.queue.put(event(1, 'A1', user_id=999))
            await asyncio.wait_for(started.wait(), 2)
            await self.transport.queue.put(event(2, 'A2', user_id=999))
            await self.transport.queue.put(event(1, 'B1', user_id=888))
            await asyncio.wait_for(other_done.wait(), 2)
            self.assertEqual(seen, ['A1', 'B1'])
            release.set()
            await asyncio.wait_for(self.transport.queue.join(), 2)
            self.assertEqual(seen, ['A1', 'B1', 'A2'])
            self.assertEqual([c.args for c in self.transport.send.call_args_list], [(888, 'B1'), (999, 'A1'), (999, 'A2')])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_global_concurrency_bound(self):
        self.bot = Bot(replace(config(), max_concurrent_requests=2), self.store, self.llm, self.transport)
        active, peak = 0, 0
        two_started, release = asyncio.Event(), asyncio.Event()
        async def complete(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
            try:
                await release.wait()
                return 'ok'
            finally:
                active -= 1
        self.llm.complete.side_effect = complete
        tasks = [asyncio.create_task(self.bot.handle(event(user_id=800+i))) for i in range(5)]
        try:
            await asyncio.wait_for(two_started.wait(), 2)
            self.assertEqual(active, 2)
            self.assertEqual(self.llm.complete.await_count, 2)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
            self.assertEqual(peak, 2)
            self.assertEqual(self.llm.complete.await_count, 5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_worker_survives_delivery_failure_for_one_friend(self):
        async def send(user_id, text):
            if user_id == 999:
                raise DeliveryError('failed')
        self.transport.send.side_effect = send
        worker = asyncio.create_task(self.bot.worker())
        try:
            await self.transport.queue.put(event(user_id=999))
            await self.transport.queue.put(event(user_id=888))
            await asyncio.wait_for(self.transport.queue.join(), 2)
            self.assertEqual(self.store.history('private:456:999', 20), [])
            self.assertEqual(self.store.history('private:456:888', 20), [('你好', '回答')])
            self.assertFalse(worker.done())
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
