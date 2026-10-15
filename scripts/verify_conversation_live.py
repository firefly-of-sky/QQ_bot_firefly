"""Opt-in billable conversation checks; synthetic events, captured QQ replies."""
import asyncio
import json
from dataclasses import replace

import aiohttp

from qqbot.bot import Bot
from qqbot.config import load
from qqbot.store import Store
from scripts.verify_live import CapturedTransport, TrackedLLM, event


async def main():
    cfg = replace(load(), owner=101, database=':memory:', auto_search=False,
                  message_merge_seconds=.15, message_merge_max_seconds=1,
                  agent_max_rounds=3, agent_max_tool_calls=3, agent_timeout_seconds=60,
                  request_timeout_seconds=45, progress_delay_seconds=10)
    store, transport, results = Store(':memory:'), CapturedTransport(), []
    session = 'private:202:101'
    async with aiohttp.ClientSession(trust_env=True) as http:
        llm = TrackedLLM(http, cfg)
        bot = Bot(cfg, store, llm, transport)
        tasks = []
        def record(name, passed, **details):
            result = {'test': name, 'passed': bool(passed), **details}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        async def launch(number, text):
            llm.entered.clear()
            task = asyncio.create_task(bot.handle(event(number, text)))
            tasks.append(task)
            await asyncio.wait_for(llm.entered.wait(), 5)
            await asyncio.sleep(.1)
            return task
        try:
            before = llm.calls
            parts = ('E2E合并：只把接下来两个词原样用斜杠连接。', '青竹', '931')
            await asyncio.gather(*(bot.handle(event(i, text)) for i, text in enumerate(parts, 1)))
            history = store.history(session, 20)
            answer = history[-1][1] if history else ''
            record('real_model_burst', len(history) == 1 and llm.calls == before + 1
                   and history[0][0] == '\n'.join(parts) and '青竹' in answer and '931' in answer,
                   answer=answer, model_calls=llm.calls - before)

            before_history, before_sent, before_cancel = len(history), len(transport.sent), llm.cancelled
            pending = await launch(4, 'E2E停止：写一篇3000字的虚构故事，尽量详细。')
            was_running = not pending.done()
            await bot.handle(event(5, '不用查了，谢谢'))
            await asyncio.wait_for(pending, 5)
            history = store.history(session, 20)
            record('real_model_natural_stop', was_running and llm.cancelled > before_cancel
                   and len(history) == before_history and len(transport.sent) == before_sent + 1
                   and '已经停下' in transport.sent[-1][1], cancelled_calls=llm.cancelled - before_cancel)

            before_cancel, before_history = llm.cancelled, len(history)
            pending = await launch(6, 'E2E更正原问题：详细比较北京和杭州的气候，至少写2000字，不需要联网。')
            was_running = not pending.done()
            await bot.handle(event(7, '我刚才说错了，是上海不是北京。只用一句话比较上海和杭州的气候，不需要联网。'))
            await asyncio.wait_for(pending, 5)
            history = store.history(session, 20)
            answer = history[-1][1] if len(history) > before_history else ''
            record('real_model_running_correction', was_running and llm.cancelled > before_cancel
                   and len(history) == before_history + 1 and '上海' in answer and '杭州' in answer
                   and {'6', '7'}.issubset(store.source_ids(session)), answer=answer,
                   cancelled_calls=llm.cancelled - before_cancel)

            tools_before = len(llm.tools)
            pending = asyncio.create_task(bot.handle(event(8, 'E2E旧问题：不用回答这个旧问题。')))
            tasks.append(pending)
            async with asyncio.timeout(2):
                while session not in bot.pending_turns:
                    await asyncio.sleep(0)
            await bot.handle(event(9, '不用查了，请必须调用计算工具算17加29，只回复结果。'))
            await pending
            history = store.history(session, 20)
            record('real_model_stop_and_new_task', '46' in history[-1][1]
                   and 'calculate' in llm.tools[tools_before:]
                   and not any('E2E旧问题' in u for u, _ in history), answer=history[-1][1])
            print(json.dumps({'passed': sum(r['passed'] for r in results), 'total': len(results),
                              'scope': 'Real model/tools; synthetic events and in-memory history; no QQ sends.'}), flush=True)
            if not all(r['passed'] for r in results):
                raise SystemExit(1)
        finally:
            await bot.close()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.close()


if __name__ == '__main__':
    asyncio.run(main())
