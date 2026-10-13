"""Opt-in live acceptance: billable API calls, synthetic messages, no QQ sends."""
import asyncio
import json
import time
from dataclasses import replace

import aiohttp
from qqbot.bot import Bot
from qqbot.config import load
from qqbot.llm import LLM
from qqbot.store import Store


class CapturedTransport:
    def __init__(self):
        self.sent = []

    async def send(self, user_id, text):
        self.sent.append((user_id, text))
        return 80000 + len(self.sent)

    async def get_message(self, message_id):
        raise AssertionError('Unexpected QQ history lookup')


class TrackedLLM(LLM):
    def __init__(self, http, cfg):
        super().__init__(http, cfg)
        self.calls = 0
        self.tools = []
        self.entered = asyncio.Event()
        self.cancelled = 0

    async def turn(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        try:
            reply = await super().turn(*args, **kwargs)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.tools.extend(call.name for call in reply.calls)
        return reply


def event(number, text, sender=101):
    return {'post_type': 'message', 'message_type': 'private', 'sub_type': 'friend',
            'self_id': 202, 'user_id': sender, 'message_id': number,
            'message': [{'type': 'text', 'data': {'text': text}}]}


async def main():
    cfg = replace(load(), owner=101, database=':memory:', agent_max_rounds=4,
                  agent_max_tool_calls=5, agent_timeout_seconds=120,
                  request_timeout_seconds=60, progress_delay_seconds=3)
    transport = CapturedTransport()
    store = Store(':memory:')
    report = []
    session = 'private:202:101'
    async with aiohttp.ClientSession(trust_env=True) as http:
        llm = TrackedLLM(http, cfg)
        bot = Bot(cfg, store, llm, transport)
        async def check(name, number, text, predicate, sender=101, expected_tools=()):
            start_calls, start_tools = llm.calls, len(llm.tools)
            started = time.monotonic()
            before_history = len(store.history(f'private:202:{sender}', 20))
            await bot.handle(event(number, text, sender))
            history = store.history(f'private:202:{sender}', 20)
            answer = history[-1][1] if len(history) > before_history else ''
            tools = llm.tools[start_tools:]
            passed = bool(answer) and predicate(answer) and set(expected_tools).issubset(tools)
            result = {'test': name, 'passed': passed, 'model_calls': llm.calls-start_calls,
                      'tools': tools, 'seconds': round(time.monotonic()-started, 1), 'answer': answer}
            if not answer and transport.sent:
                result['captured_error'] = transport.sent[-1][1]
            report.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

        try:
            await check('conversation_setup', 1, '这是测试对话。请记住测试暗号是青柠842，只回复收到。', lambda a: '收到' in a)
            await check('conversation_followup', 2, '刚才的测试暗号是什么？只回复暗号。', lambda a: '青柠842' in a)
            await check('other_user_isolation', 1, '我们之前有没有约定过测试暗号？请直接回答，不要猜。', lambda a: '青柠842' not in a, sender=303)
            await check('real_calculation', 3, '请调用计算工具算 (125+75)*12，只回复计算结果。', lambda a: '2400' in a.replace(',', ''), expected_tools=('calculate',))
            await check('real_search_and_page', 4, '/search 请搜索 Python asyncio.TaskGroup 官方文档，然后用 read_page 读取找到的一个官方网页，用一句话说明 TaskGroup 的用途。', lambda a: 'TaskGroup' in a, expected_tools=('web_search','read_page'))
            before = len(store.history(session, 20))
            llm.entered.clear()
            pending = asyncio.create_task(bot.handle(event(5, '请详细讲解 asyncio 的任务取消机制，给出三个完整例子。')))
            try:
                await asyncio.wait_for(llm.entered.wait(), 5)
                await asyncio.sleep(.2)
                was_active = not pending.done()
                await bot.handle(event(6, '/cancel'))
                await asyncio.wait_for(pending, 5)
                result = {'test':'cancel_real_generation', 'passed':was_active and llm.cancelled > 0 and len(store.history(session,20)) == before and transport.sent[-1][1] == '已取消当前及排队的问题。', 'inflight_model_cancelled':llm.cancelled > 0}
                report.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            async with http.ws_connect(cfg.ws_url, headers={'Authorization':'Bearer '+cfg.token},timeout=aiohttp.ClientWSTimeout(ws_receive=10)) as ws:
                await ws.send_json({'action':'get_status','params':{},'echo':'live-verification-status'})
                async for frame in ws:
                    if frame.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(frame.data)
                    if data.get('echo') == 'live-verification-status':
                        status=data.get('data') or {}
                        result={'test':'real_qq_connection','passed':data.get('status')=='ok' and status.get('online') is True,'online':status.get('online'),'good':status.get('good')}
                        report.append(result)
                        print(json.dumps(result, ensure_ascii=False), flush=True)
                        break
            print(json.dumps({'summary': {'passed':sum(bool(r['passed']) for r in report),'total':len(report)},'scope':'Real model and search; synthetic QQ events; captured replies; read-only QQ status. No real QQ messages sent.'}), flush=True)
            if not all(r['passed'] for r in report):
                raise SystemExit(1)
        finally:
            await bot.close()
            store.close()

if __name__ == "__main__":
    asyncio.run(main())
