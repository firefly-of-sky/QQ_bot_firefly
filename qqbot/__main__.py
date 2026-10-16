import argparse
import asyncio
import logging
import os
import re
import shlex
import signal
from dataclasses import replace
from pathlib import Path

import aiohttp

from . import __version__
from .bot import Bot
from .config import load
from .llm import LLM, ModelError, messages
from .onebot import OneBot
from .store import Store
from .search import Search
from .agent import Agent
from .images import ImageLoader
from .progress import ProgressState
from .runtime import ConversationRuntime
from .tools import build_tools


def load_env_file(filename):
    """Explicit local CLI convenience; no shell execution or variable expansion."""
    for line in Path(filename).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("env format")
        parts = shlex.split(value, comments=True)
        if len(parts) > 1:
            raise ValueError("quote env values containing spaces")
        os.environ.setdefault(key, parts[0] if parts else "")


async def main():
    parser = argparse.ArgumentParser(description="QQbot conversational agent")
    parser.add_argument('--version', action='version', version=f'QQbot {__version__}')
    parser.add_argument("--env-file", help="Load a local env file without executing it; existing environment wins")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-config", action="store_true")
    mode.add_argument("--check-model", metavar="ALIAS", help="Make one billable API test request without connecting QQ")
    mode.add_argument("--check-search", action="store_true", help="Make one search API connectivity test")
    mode.add_argument("--check-agent", metavar="ALIAS", help="Verify model/tool/result loop with bounded billable requests; no QQ")
    mode.add_argument("--ask", metavar="TEXT", help="Run one local conversation turn; no QQ connection or saved history")
    parser.add_argument("--model", metavar="ALIAS", help="Model for --ask; defaults to default_model")
    args = parser.parse_args()
    try:
        if args.env_file:
            load_env_file(args.env_file)
        cfg = load()
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("配置无效：请检查 config.toml、人格文件、OWNER_QQ、ONEBOT_TOKEN 和各模型密钥环境变量。")
    if args.check_config:
        print("配置有效；模型别名：" + ", ".join(cfg.models))
        return
    if args.check_search:
        try:
            results = await Search(cfg).query("Python 官方文档")
        except ModelError as exc:
            raise SystemExit(str(exc))
        print(f"搜索调用成功，收到 {len(results)} 条带链接的结果。")
        return
    async with aiohttp.ClientSession(trust_env=True) as http:
        llm = LLM(http, cfg)
        if args.check_agent or args.ask is not None:
            alias = args.check_agent or args.model or cfg.default_model
            if alias not in cfg.models:
                raise SystemExit("模型别名不存在。")
            model = cfg.models[alias]
            state = ProgressState()
            try:
                if args.check_agent:
                    if not model.supports_tools:
                        raise ModelError("该模型已设置 supports_tools=false，请启用工具调用后检查。")
                    probe = replace(cfg, agent_max_rounds=2, agent_max_tool_calls=2, agent_timeout_seconds=60)
                    registry = build_tools(probe, Search(probe), state, web_enabled=False)
                    result = await Agent(probe, llm, registry).run(model, [],
                        "请必须调用 calculate 工具计算 17*29，读取工具结果后用一句话告诉我答案。")
                    if "calculate" not in result.tools_used:
                        raise ModelError("模型未实际调用计算工具；仅返回文字不能证明 Agent 接口可用。")
                    print(f"Agent 验证成功：模型调用 {result.model_calls} 次，工具调用 {result.tool_calls} 次，已收到最终回复。")
                else:
                    if not args.ask.strip() or len(args.ask) > cfg.max_input_chars:
                        raise ModelError("问题为空或超过输入长度限制。")
                    runtime = ConversationRuntime(cfg, llm, ImageLoader(cfg), Search(cfg))
                    print(await runtime.respond(model, [], args.ask, [], args.ask, False, state))
            except ModelError as exc:
                raise SystemExit(str(exc))
            return
        if args.check_model:
            if args.check_model not in cfg.models:
                raise SystemExit("模型别名不存在。")
            try:
                await llm.complete(cfg.models[args.check_model], messages(cfg, [], "请只回复：连接成功"))
            except ModelError as exc:
                raise SystemExit(str(exc))
            print("模型调用成功，已收到文字回复。")
            return
        from .tulpa.app import Application
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,asyncio.current_task().cancel)
        await Application(cfg, llm, http).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for logger in ('httpx','mcp'):
        logging.getLogger(logger).setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
