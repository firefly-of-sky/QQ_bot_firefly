"""Task-aware progress wording without an extra model request."""
import random
import re
from collections import OrderedDict
from dataclasses import dataclass

DEFAULT_TEMPLATES = {
    'queued': ['稍等一下，我这边还没轮到这条，马上接着看哦', '这条我收到了，稍等我一下哦'],
    'search': ['稍等稍等，我还在找呢', '等我一下，我再找找相关的信息哦', '我还在查资料，稍等一下哦'],
    'reading': ['我打开资料再看看，稍等一下哦', '等我看看原文里的细节哦'],
    'image': ['稍等一下，我还在看这张图呢', '等我一下，我再看看图里的细节哦'],
    'reasoning': ['稍等稍等，我还在想这道题呢', '等我捋一捋这道题的思路哦', '这题我再想一下，稍等我哦'],
    'writing': ['稍等一下，我还在组织文字呢', '我再理一理怎么写，等我一下哦'],
    'coding': ['稍等一下，我还在梳理代码逻辑呢', '这段代码我再想想怎么写，等我一下哦'],
    'summary': ['资料找到了，我再整理一下怎么跟你说哦', '我正在梳理找到的信息，稍等一下哦'],
    'thinking': ['稍等一下，我还在想怎么回答你呢', '等我捋一捋，马上接着说哦', '我再想一下怎么说更清楚，稍等我哦'],
}


def task_kind(query: str, has_images: bool) -> str:
    if re.search(r'解题|题目|第.{0,4}题|这题|这道题|证明|求解|方程|计算|几何|微积分|数学', query):
        return 'reasoning'
    if re.search(r'代码|编程|程序|debug|bug|python|javascript|sql', query, re.I):
        return 'coding'
    if re.search(r'写作|作文|写.{0,8}(文章|故事|文案|邮件|小说|报告)|润色|改写|翻译', query):
        return 'writing'
    return 'image' if has_images else 'thinking'


@dataclass
class ProgressState:
    stage: str = 'queued'


class ProgressWording:
    def __init__(self, overrides):
        self.templates = DEFAULT_TEMPLATES | overrides
        self.previous = OrderedDict()

    def choose(self, session: str, stage: str) -> str:
        options = self.templates.get(stage, self.templates['thinking'])
        previous = self.previous.get(session)
        selected = random.choice([text for text in options if text != previous] or options)
        self.previous[session] = selected
        self.previous.move_to_end(session)
        while len(self.previous) > 500:
            self.previous.popitem(last=False)
        return selected + '，可以输入/cancel取消哦'
