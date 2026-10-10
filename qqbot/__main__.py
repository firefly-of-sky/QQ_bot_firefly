import argparse
import asyncio
import logging

import aiohttp

from .bot import Bot
from .config import load
from .llm import LLM, ModelError, messages
from .onebot import OneBot
from .store import Store


async def main():
    parser = argparse.ArgumentParser(description="QQ private assistant")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-model", metavar="ALIAS", help="Make one billable API test request without connecting QQ")
    args = parser.parse_args()
    try:
        cfg = load()
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("配置无效：请检查 config.toml、OWNER_QQ、ONEBOT_TOKEN 和各模型密钥环境变量。")
    if args.check_config:
        print("配置有效；模型别名：" + ", ".join(cfg.models))
        return
    async with aiohttp.ClientSession(trust_env=True) as http:
        llm = LLM(http, cfg)
        if args.check_model:
            if args.check_model not in cfg.models:
                raise SystemExit("模型别名不存在。")
            try:
                await llm.complete(cfg.models[args.check_model], messages(cfg, [], "请只回复：连接成功"))
            except ModelError as exc:
                raise SystemExit(str(exc))
            print("模型调用成功，已收到文字回复。")
            return
        store = Store(cfg.database)
        transport = OneBot(http, cfg.ws_url, cfg.token)
        bot = Bot(cfg, store, llm, transport)
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(transport.run(bot.accepts))
                group.create_task(bot.worker())
        finally:
            store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
