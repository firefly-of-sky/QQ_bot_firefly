"""Short-lived message references, scoped to one private conversation."""
import time
from collections import OrderedDict

from .images import ImageError, parse_message
from .onebot import DeliveryError


class References:
    def __init__(self, cfg, transport):
        self.cfg, self.transport = cfg, transport
        self.cache = OrderedDict()

    def remember(self, session, message_id, message):
        if type(message_id) not in (str, int):
            return
        self.cache[(session, str(message_id))] = (time.monotonic(), message)
        self.prune()

    def prune(self):
        cutoff = time.monotonic() - self.cfg.media_ttl_seconds
        for key, (stamp, _) in list(self.cache.items()):
            if stamp < cutoff:
                del self.cache[key]
        while len(self.cache) > 500:
            self.cache.popitem(last=False)

    def clear(self, session):
        for key in list(self.cache):
            if key[0] == session:
                del self.cache[key]

    async def resolve(self, session, user_id, self_id, message):
        if not isinstance(message, list):
            text, images = parse_message(message, self.cfg.max_images)
            return text, images, ''
        replies = [s for s in message if isinstance(s, dict) and s.get('type') == 'reply']
        if not replies:
            text, images = parse_message(message, self.cfg.max_images)
            return text, images, ''
        if len(replies) != 1 or not isinstance(replies[0].get('data'), dict):
            raise ImageError('无法读取这条引用，请重新发送相关内容。')
        reference = replies[0]['data'].get('id')
        if type(reference) not in (str, int):
            raise ImageError('无法读取这条引用，请重新发送相关内容。')
        clean = [s for s in message if s not in replies and not (
            isinstance(s, dict) and s.get('type') == 'at' and isinstance(s.get('data'), dict)
            and str(s['data'].get('qq')) == str(self_id))]
        text, images = parse_message(clean, self.cfg.max_images) if clean else ('', [])
        self.prune()
        cached = self.cache.get((session, str(reference)))
        if cached:
            quoted = cached[1]
        else:
            try:
                data = await self.transport.get_message(reference)
            except DeliveryError as exc:
                raise ImageError('引用消息已失效或无法读取，请重新发送相关内容。') from exc
            # get_msg has no reliable recipient field for bot-authored messages.
            # Only accept uncached incoming messages from this exact private sender.
            if (not isinstance(data, dict) or data.get('message_type') != 'private'
                    or data.get('group_id') or not isinstance(data.get('sender'), dict)
                    or str(data['sender'].get('user_id')) != str(user_id)):
                raise ImageError('无法确认引用属于当前私聊，请重新发送相关内容。')
            quoted = data.get('message')
        if isinstance(quoted, list):
            quoted = [s for s in quoted if not (isinstance(s, dict) and s.get('type') in ('reply', 'at'))]
        quote_text, quote_images = parse_message(quoted, self.cfg.max_images)
        images = list(dict.fromkeys(images + quote_images))
        if len(images) > self.cfg.max_images:
            raise ImageError(f'每次最多处理 {self.cfg.max_images} 张图片，请分批发送。')
        if len(quote_text) > self.cfg.max_input_chars:
            raise ImageError('引用内容过长，请选取相关部分重新发送。')
        return text, images, quote_text
