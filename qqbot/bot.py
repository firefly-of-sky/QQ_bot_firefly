import asyncio
import logging
import time
from collections import deque
from weakref import WeakValueDictionary

from .config import Config
from .formatting import to_plain_text
from .images import ImageError, ImageLoader, parse_message
from .llm import ModelError, messages
from .onebot import DeliveryError
from .store import Store

log = logging.getLogger(__name__)
HELP = "命令：\n/help 查看帮助\n/reset 清空自己的上下文\n/status 查看自己的状态\n可发送文字、图片或同条图文消息。通过好友申请后即可私聊；图片识别需要支持视觉的模型。"
OWNER_HELP = "\n主人命令：\n/models 查看模型\n/model 别名 切换自己的模型并清空上下文"


def qq_id(value) -> int | None:
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        number = int(value)
        return number if number > 0 else None
    return None


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
        self.locks = WeakValueDictionary()
        self.model_slots = asyncio.Semaphore(cfg.max_concurrent_requests)
        self.requests: dict[str, deque[float]] = {}

    def accepts(self, event: dict) -> bool:
        sender, account = qq_id(event.get("user_id")), qq_id(event.get("self_id"))
        return (event.get("post_type") == "message"
                and event.get("message_type") == "private"
                and event.get("sub_type") == "friend"
                and sender is not None and account is not None and sender != account
                and type(event.get("message_id")) in (int, str))

    async def reply(self, user_id: int, text: str):
        for chunk in split_reply(text, self.cfg.reply_chunk_chars):
            await self.transport.send(user_id, chunk)

    def allow_request(self, session: str) -> bool:
        now = time.monotonic()
        for key in list(self.requests):
            window = self.requests[key]
            while window and window[0] <= now - 60:
                window.popleft()
            if not window:
                del self.requests[key]
        window = self.requests.setdefault(session, deque())
        if len(window) >= self.cfg.requests_per_minute:
            return False
        window.append(now)
        return True

    async def handle(self, event: dict):
        if not self.accepts(event):
            return
        user_id = qq_id(event["user_id"])
        session = f"private:{qq_id(event['self_id'])}:{user_id}"
        lock = self.locks.setdefault(session, asyncio.Lock())
        async with lock:
            if not self.store.claim(f"{session}:{event['message_id']}"):
                return
            try:
                text, image_urls = parse_message(event.get("message"), self.cfg.max_images)
            except ImageError as exc:
                await self.reply(user_id, str(exc))
                return
            if not text and not image_urls:
                return
            alias = self.store.model(session, self.cfg.default_model)
            if user_id != self.cfg.owner and alias != self.cfg.default_model:
                self.store.reset(session, self.cfg.default_model)
                alias = self.cfg.default_model
            if text.startswith("/") and not image_urls:
                await self.command(user_id, session, alias, text)
                return
            if len(text) > self.cfg.max_input_chars:
                await self.reply(user_id, f"消息过长，请控制在 {self.cfg.max_input_chars} 字符以内。")
                return
            if alias not in self.cfg.models:
                await self.reply(user_id, "当前模型已从配置中移除，请联系主人检查模型配置。")
                return
            if image_urls and not self.cfg.models[alias].supports_images:
                await self.reply(user_id, "当前模型已配置为仅支持文字，请联系主人配置支持图片的模型。")
                return
            if image_urls and not text:
                text = "请描述图片中的主要内容，并识别其中清晰可见的文字；不确定的部分请说明。"
            if not self.allow_request(session):
                await self.reply(user_id, f"提问过于频繁，每人每分钟最多 {self.cfg.requests_per_minute} 次，请稍后再试。")
                return
            try:
                history = self.store.history(session, self.cfg.history_turns)
                conversation = messages(self.cfg, history, text)
                async with self.model_slots:
                    if image_urls:
                        images = await self.image_loader.load(image_urls)
                        conversation = messages(self.cfg, history, text, images)
                    answer = await self.llm.complete(self.cfg.models[alias], conversation)
            except (ModelError, ImageError) as exc:
                await self.reply(user_id, str(exc))
                return
            await self.reply(user_id, to_plain_text(answer))
            # Only complete, acknowledged deliveries become conversation history.
            saved_text = text
            if image_urls:
                saved_text += f"\n[本轮附带 {len(image_urls)} 张图片；原图未保留，后续只能参考文字分析，重新检查细节需要用户重发图片。]"
            self.store.append(session, saved_text, answer, self.cfg.history_turns)

    async def command(self, user_id: int, session: str, alias: str, text: str):
        parts = text.split()
        owner = user_id == self.cfg.owner
        if parts and parts[0] in ("/model", "/models") and not owner:
            result = "模型管理命令仅主人可用。你可以直接聊天，或用 /reset 清空自己的上下文。"
        elif parts == ["/help"]:
            result = HELP + (OWNER_HELP if owner else "")
        elif parts == ["/models"]:
            result = "可用模型：\n" + "\n".join(f"{'→ ' if name == alias else ''}{name} ({model.model}) / {'图片已启用' if model.supports_images else '仅文字'}" for name, model in self.cfg.models.items())
        elif parts == ["/status"]:
            count = len(self.store.history(session, self.cfg.history_turns))
            model = self.cfg.models.get(alias)
            mode = "文字与图片" if model and model.supports_images else "仅文字"
            model_info = f"\n当前模型：{alias}" if owner else ""
            result = f"QQ 已连接{model_info}\n已保存问答：{count} 轮\n模式：好友私聊 / {mode}"
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
        await self.reply(user_id, result)

    async def _process_event(self, event: dict, slots: asyncio.Semaphore):
        try:
            await self.handle(event)
        except DeliveryError:
            log.warning("QQ reply failed or unconfirmed; not retrying to avoid duplicates")
        except Exception as exc:
            log.error("Event failed (%s); details omitted to protect message content", type(exc).__name__)
        finally:
            self.transport.queue.task_done()
            slots.release()

    async def worker(self):
        # Tasks waiting on their own session lock do not occupy model slots.
        # The separate dispatch bound prevents an unbounded number of tasks.
        slots = asyncio.Semaphore(100)
        async with asyncio.TaskGroup() as group:
            while True:
                await slots.acquire()
                try:
                    event = await self.transport.queue.get()
                except BaseException:
                    slots.release()
                    raise
                group.create_task(self._process_event(event, slots))
