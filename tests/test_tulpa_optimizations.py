"""Quality and dispatch boundaries for the two opt-in latency experiments."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from qqbot.store import Store
from qqbot.tulpa.optimizations import compact_context, join_short_text_bubbles
from qqbot.tulpa.settings import load_settings


def message(mid, text, own=False, **extra):
    return dict(id=mid, onebot_message_id=str(mid+100), is_self=own, content=text,
                sender_id='456' if own else '123', timestamp=1700000000000,
                segments=[{'type': 'text', 'text': text}], source='onebot_websocket',
                received_at=1700000000, conversation_id='456:direct:123') | extra


class ContextCompaction(unittest.TestCase):
    def test_quality_rubric_rejects_constraint_mentions_with_contradictory_advice(self):
        from scripts.benchmark_tulpa_latency import check
        self.assertFalse(check('memory',['1800元以内，避开花生，周末旅行，周五晚出发，口令海棠642。'],[]))
        self.assertTrue(check('memory',['1800元以内，避开花生，仅周六或周日出行，不占工作日，口令海棠642。'],[]))

    def test_only_proven_duplicates_removed_and_current_input_preserved(self):
        recent=[message(1,'预算1800，不吃花生'),message(2,'记住了',True),message(3,'继续')]
        payload=dict(input_batch={'id':'batch'},new_messages=[recent[-1]],
                     context={'messages':recent},guidance={'reminders':['保持人格'],'continue_with':{'tool':'wait'}})
        original=deepcopy(payload)
        records=[dict(user='预算1800，不吃花生',assistant='记住了',sources=['101']),
                 dict(user='更早的口令海棠642',assistant='好的',sources=['99'])]
        packed,history=compact_context(payload,records)
        self.assertEqual(history,[('更早的口令海棠642','好的')])
        self.assertEqual([m['id'] for m in packed['context']['messages']],[1,2])
        self.assertEqual(packed['new_messages'][0]['content'],'继续')
        self.assertNotIn('received_at',packed['new_messages'][0])
        self.assertEqual(packed['guidance'],{'reminders':['保持人格']})
        self.assertEqual(payload,original)

    def test_archive_kept_when_sources_or_reply_are_missing_or_ambiguous(self):
        cases=[([],False),(['999'],False),(['101'],True)]
        for sources,recall in cases:
            recent=[message(1,'预算1800',recalled=recall),message(2,'记住了',True)]
            _,history=compact_context({'context':{'messages':recent}},
                [dict(user='预算1800',assistant='记住了',sources=sources)])
            self.assertEqual(history,[('预算1800','记住了')])
        for recent in ([message(1,'预算1800')],
                       [message(1,'预算1800'),message(2,'部分回答',True)],
                       [message(1,'预算1800'),message(2,'追问'),message(3,'记住了',True)]):
            _,history=compact_context({'context':{'messages':recent}},
                [dict(user='预算1800',assistant='记住了',sources=['101'])])
            self.assertEqual(len(history),1)

    def test_burst_and_multi_bubble_history_require_complete_text(self):
        recent=[message(1,'青羽'),message(2,'615'),message(3,'第一句',True),message(4,'第二句',True)]
        record=dict(user='青羽\n615',assistant='第一句\n第二句',sources=['101','102'])
        _,history=compact_context({'context':{'messages':recent}},[record])
        self.assertEqual(history,[])
        _,history=compact_context({'context':{'messages':recent[1:]}},[record])
        self.assertEqual(len(history),1)

    def test_media_reference_recall_and_unknown_semantics_survive(self):
        new=message(3,'[图片]',segments=[{'type':'reply','onebot_message_id':'101'},
                                     {'type':'image','image_index':0}],custom_semantics={'retain':True},synthetic=False)
        old=message(1,'撤回后不能泄漏',recalled=True)
        changed=dict(new,recalled=True)
        packed,_=compact_context({'new_messages':[new], 'context':{'messages':[old,changed]}},[])
        self.assertEqual(packed['new_messages'][0]['segments'],new['segments'])
        self.assertEqual(packed['new_messages'][0]['custom_semantics'],{'retain':True})
        self.assertIs(packed['new_messages'][0]['synthetic'],False)
        self.assertEqual(packed['context']['messages'][0],{'id':1,'onebot_message_id':'101','recalled':True})
        self.assertEqual(len(packed['context']['messages']),2)

    def test_source_history_is_scoped_ordered_and_responds_to_recall(self):
        store=Store(':memory:')
        try:
            store.append('s','第一句','回答一',20,['101'])
            store.append('other','其他人','不可混入',20,['500'])
            store.append('s','第二句','回答二',20,['102','103'])
            rows=store.history_with_sources('s',20)
            self.assertEqual([r['user'] for r in rows],['第一句','第二句'])
            self.assertEqual(set(rows[-1]['sources']),{'102','103'})
            store.recall('s','102')
            self.assertEqual([r['user'] for r in store.history_with_sources('s',20)],['第一句'])
        finally:store.close()


class SingleBubble(unittest.TestCase):
    def test_preserves_all_words_newlines_and_unicode_without_mutation(self):
        bubbles=[{'kind':'text','text':'预算≤1800😊\n不能吃花生'}, {'kind':'text','text':'只能周末出行'}]
        before=deepcopy(bubbles)
        joined=join_short_text_bubbles(bubbles,['给我两点建议'])
        self.assertEqual(joined,[{'kind':'text','text':'预算≤1800😊\n不能吃花生\n只能周末出行'}])
        self.assertEqual(bubbles,before)
        self.assertEqual(join_short_text_bubbles(joined,['建议']),joined)

    def test_explicit_separate_messages_long_media_addressing_and_invalid_queues_stay(self):
        short=[{'kind':'text','text':'甲'},{'kind':'text','text':'乙'}]
        for request in ['请分两条消息发给我','拆成两条发送','分别发送','不要合并','一条一条发',
                        '用两个气泡','分开发','Send two messages','use separate bubbles']:
            self.assertIs(join_short_text_bubbles(short,[request]),short)
        examples=[short+[{'kind':'sticker','sticker_id':'a'*32}],
                  [{'kind':'text','text':'甲','quote':True},short[1]],
                  [{'kind':'text','text':'甲'*300},short[1]],
                  [{'kind':'text','text':''},short[1]],
                  [{'kind':'text','text':'甲','unexpected':True},short[1]],[],None]
        for value in examples:self.assertIs(join_short_text_bubbles(value,['回答']),value)

    def test_performance_settings_are_independent_opt_in_booleans(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.toml'
            with patch.dict('os.environ',{'TULPA_CONFIG':str(p)}):
                self.assertEqual(load_settings()['performance'],dict(compact_context=False,single_bubble_short_replies=False))
                p.write_text('[performance]\ncompact_context=true\n')
                self.assertEqual(load_settings()['performance'],dict(compact_context=True,single_bubble_short_replies=False))
                for text in ['compact_context="yes"','single_bubble_short_replies=1','unknown=true']:
                    p.write_text('[performance]\n'+text)
                    with self.assertRaises(ValueError):load_settings()
