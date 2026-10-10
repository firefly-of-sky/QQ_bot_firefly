import asyncio
import logging

from .config import Config
from .formatting import to_plain_text
from .images import ImageError, ImageLoader, parse_message
from .llm import ModelError, messages
from .onebot import DeliveryError
from .store import Store

log = logging.getLogger(__name__)
HELP = "命令：\n/help 查看帮助\n/models 查看模型\n/model 别名 切换模型并清空上下文\n/reset 清空当前上下文\n/status 查看状态\n可发送文字、图片或同条图文消息。仅支持主人私聊；图片识别需要支持视觉的模型。"


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


class Bot:
    def __init__(self, cfg: Config, store: Store, llm, transport, image_loader=None):
        self.cfg, self.store, self.llm, self.transport = cfg, store, llm, transport
        self.image_loader = image_loader or ImageLoader(cfg)
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
            try:
                text, image_urls = parse_message(event.get("message"), self.cfg.max_images)
            except ImageError as exc:
                await self.reply(str(exc))
                return
            if not text and not image_urls:
                return
            alias = self.store.model(session, self.cfg.default_model)
            if text.startswith("/") and not image_urls:
                await self.command(session, alias, text)
                return
            if len(text) > self.cfg.max_input_chars:
                await self.reply(f"消息过长，请控制在 {self.cfg.max_input_chars} 字符以内。")
                return
            if alias not in self.cfg.models:
                await self.reply("当前模型已从配置中移除，请用 /models 查看并通过 /model 别名 切换。")
                return
            if image_urls and not self.cfg.models[alias].supports_images:
                await self.reply("当前模型已配置为仅支持文字，请用 /model 切换到支持图片的模型。")
                return
            if image_urls and not text:
                text = "请描述图片中的主要内容，并识别其中清晰可见的文字；不确定的部分请说明。"
            try:
                history = self.store.history(session, self.cfg.history_turns)
                conversation = messages(self.cfg, history, text)
                if image_urls:
                    images = await self.image_loader.load(image_urls)
                    conversation = messages(self.cfg, history, text, images)
                answer = await self.llm.complete(self.cfg.models[alias], conversation)
            except (ModelError, ImageError) as exc:
                await self.reply(str(exc))
                return
            await self.reply(to_plain_text(answer))
            # Only complete, acknowledged deliveries become conversation history.
            saved_text = text
            if image_urls:
                saved_text += f"\n[本轮附带 {len(image_urls)} 张图片；原图未保留，后续只能参考文字分析，重新检查细节需要用户重发图片。]"
            self.store.append(session, saved_text, answer, self.cfg.history_turns)

    async def command(self, session: str, alias: str, text: str):
        parts = text.split()
        if parts == ["/help"]:
            result = HELP
        elif parts == ["/models"]:
            result = "可用模型：\n" + "\n".join(f"{'→ ' if name == alias else ''}{name} ({model.model}) / {'图片已启用' if model.supports_images else '仅文字'}" for name, model in self.cfg.models.items())
        elif parts == ["/status"]:
            count = len(self.store.history(session, self.cfg.history_turns))
            model = self.cfg.models.get(alias)
            mode = "文字与图片" if model and model.supports_images else "仅文字"
            result = f"QQ 已连接\n当前模型：{alias}\n已保存问答：{count} 轮\n模式：主人私聊 / {mode}"
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
