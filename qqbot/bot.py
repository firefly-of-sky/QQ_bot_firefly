import asyncio
import logging

from .config import Config
from .llm import ModelError, messages
from .onebot import DeliveryError
from .store import Store

log = logging.getLogger(__name__)
HELP = "命令：\n/help 查看帮助\n/models 查看模型\n/model 别名 切换模型并清空上下文\n/reset 清空当前上下文\n/status 查看状态\n直接发送文字即可聊天。第一版仅支持主人私聊。"


def split_reply(text: str, limit: int) -> list[str]:
    result = []
    while len(text) > limit:
        end = text.rfind("\n", 0, limit + 1)
        if end <= 0:
            end = limit
        else:
            end += 1
        # A newline exactly at limit must not make the chunk too long.
        end = min(end, limit)
        result.append(text[:end])
        text = text[end:]
    if text:
        result.append(text)
    return result


def extract_text(message) -> str | None:
    # Require array-format events; never interpret a CQ string as ordinary text.
    if not isinstance(message, list) or not message:
        return None
    parts = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            return None
        data = segment.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            return None
        parts.append(data["text"])
    return "".join(parts).strip()


class Bot:
    def __init__(self, cfg: Config, store: Store, llm, transport):
        self.cfg, self.store, self.llm, self.transport = cfg, store, llm, transport
        self.lock = asyncio.Lock()

    def accepts(self, event: dict) -> bool:
        return (event.get("post_type") == "message"
                and event.get("message_type") == "private"
                and event.get("sub_type") == "friend"
                and str(event.get("user_id")) == str(self.cfg.owner)
                and str(event.get("self_id")) != str(self.cfg.owner)
                and isinstance(event.get("self_id"), (int, str))
                and isinstance(event.get("message_id"), (int, str)))

    async def reply(self, text: str):
        for chunk in split_reply(text, self.cfg.reply_chunk_chars):
            await self.transport.send(self.cfg.owner, chunk)

    async def handle(self, event: dict):
        if not self.accepts(event):
            return
        async with self.lock:
            session = f"private:{event['self_id']}:{self.cfg.owner}"
            if not self.store.claim(f"{session}:{event['message_id']}"):
                return
            text = extract_text(event.get("message"))
            if text is None:
                await self.reply("第一版仅支持纯文字消息，请直接发送文字。")
                return
            if not text:
                return
            alias = self.store.model(session, self.cfg.default_model)
            if text.startswith("/"):
                await self.command(session, alias, text)
                return
            if len(text) > self.cfg.max_input_chars:
                await self.reply(f"消息过长，请控制在 {self.cfg.max_input_chars} 字符以内。")
                return
            if alias not in self.cfg.models:
                await self.reply("当前模型已从配置中移除，请用 /models 查看并通过 /model 别名 切换。")
                return
            try:
                history = self.store.history(session, self.cfg.history_turns)
                answer = await self.llm.complete(self.cfg.models[alias], messages(self.cfg, history, text))
            except ModelError as exc:
                await self.reply(str(exc))
                return
            await self.reply(answer)
            # Only complete, acknowledged deliveries become conversation history.
            self.store.append(session, text, answer, self.cfg.history_turns)

    async def command(self, session: str, alias: str, text: str):
        parts = text.split()
        if parts == ["/help"]:
            result = HELP
        elif parts == ["/models"]:
            result = "可用模型：\n" + "\n".join(f"{'→ ' if name == alias else ''}{name} ({model.model})" for name, model in self.cfg.models.items())
        elif parts == ["/status"]:
            count = len(self.store.history(session, self.cfg.history_turns))
            result = f"QQ 已连接\n当前模型：{alias}\n已保存问答：{count} 轮\n模式：主人私聊 / 纯文字"
        elif parts == ["/reset"]:
            self.store.reset(session)
            result = "已清空当前上下文。"
        elif len(parts) == 2 and parts[0] == "/model":
            if parts[1] not in self.cfg.models:
                result = "模型不存在，请用 /models 查看可用别名。"
            else:
                self.store.reset(session, parts[1])
                result = f"已切换到 {parts[1]}，并清空上下文。"
        else:
            result = "命令无效，请用 /help 查看用法。"
        await self.reply(result)

    async def worker(self):
        while True:
            event = await self.transport.queue.get()
            try:
                await self.handle(event)
            except DeliveryError:
                log.warning("QQ reply failed or unconfirmed; not retrying to avoid duplicates")
            except Exception as exc:
                log.error("Event failed (%s); details omitted to protect message content", type(exc).__name__)
            finally:
                self.transport.queue.task_done()
