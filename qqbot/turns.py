"""Bounded message bursts and explicit conversational control phrases."""
import re
import time
from dataclasses import dataclass, field


STOP = re.compile(r"^(?:不用查了|不用搜了|不用回答了|不用继续了|别查了|别搜了|别回答了|别继续了|停止搜索|停止回答|停止处理|取消刚才的问题|取消刚才的任务|先停一下|先别回答|停一下|算了不问了)(?=$|[，,。.!！；;\s])")
CORRECTION = re.compile(r"^(?:(?:我)?(?:刚才|前面|上面)(?:说错了|写错了|打错了|弄错了)|(?:我)?说错了|更正一下|纠正一下|改成|换成)")


def plain_text(raw):
    if not isinstance(raw, list) or not raw:
        return None
    if any(not isinstance(s, dict) or s.get('type') != 'text'
           or not isinstance(s.get('data'), dict) or not isinstance(s['data'].get('text'), str)
           for s in raw):
        return None
    return ''.join(s['data']['text'] for s in raw).strip()


def control(text):
    """Only standalone, unquoted text is passed here; never classify references."""
    if text is None:
        return None, ''
    text = text.strip()
    match = STOP.match(text)
    if match:
        tail = text[match.end():].lstrip('，,。.!！；; \n')
        if tail.rstrip('，,。.!！~～ ') in ('', '谢谢', '谢谢你', '先这样', '暂时不用了'):
            tail = ''
        return 'stop', tail
    if CORRECTION.match(text):
        return 'correct', text
    return None, text


def source_ids(event):
    sources = {str(event['message_id'])}
    raw = event.get('message')
    if isinstance(raw, list):
        for segment in raw:
            if isinstance(segment, dict) and segment.get('type') == 'reply':
                data = segment.get('data')
                if isinstance(data, dict) and type(data.get('id')) in (str, int):
                    sources.add(str(data['id']))
    return sources


def mergeable(event):
    raw = event.get('message')
    if not isinstance(raw, list) or not raw:
        return False
    if any(not isinstance(s, dict) or s.get('type') not in ('text', 'image', 'reply', 'at') for s in raw):
        return False
    text = ''.join(s.get('data', {}).get('text', '') for s in raw
                   if s.get('type') == 'text' and isinstance(s.get('data'), dict)
                   and isinstance(s['data'].get('text'), str)).strip()
    return not text.startswith('/') or text.startswith('/search ')


def text_size(parts):
    return sum(len(s.get('data', {}).get('text', '')) for e in parts for s in e.get('message', [])
               if isinstance(s, dict) and s.get('type') == 'text' and isinstance(s.get('data'), dict)
               and isinstance(s['data'].get('text'), str)) + max(0, len(parts) - 1)


@dataclass(eq=False)
class Turn:
    parts: list
    sources: set
    opened: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    sealed: bool = False
    task: object = None
    wake: object = None
    prepared: dict | None = None
    resume: dict | None = None

    def event(self):
        result = dict(self.parts[-1])
        if len(self.parts) > 1:
            raw = []
            for event in self.parts:
                if raw:
                    raw.append({'type': 'text', 'data': {'text': '\n'}})
                raw.extend(event['message'])
            result['message'] = raw
        return result
