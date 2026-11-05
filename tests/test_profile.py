import asyncio,json,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import unittest
from qqbot.store import Store
from qqbot.llm import ModelReply
from qqbot.tulpa.profile import ProfileMemory,profile_context,validate_updates

class ProfileTests(unittest.TestCase):
 def row(self,value='喜欢简短回答',confidence='explicit',source='1'):
  return dict(key='回答风格',value=value,kind='preference',confidence=confidence,sources=[source])
 def test_restart_reset_isolation_correction_and_inference_priority(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=str(Path(tmp)/'chat.db');s=Store(p);s.append('a','user','assistant',2);s.save_profile('a',0,[self.row()]);s.reset('a');s.close();s=Store(p)
   self.assertEqual(s.history('a',2),[]);self.assertEqual(s.profile('b'),[]);self.assertEqual(s.profile('a')[0]['value'],'喜欢简短回答')
   s.save_profile('a',0,[self.row('喜欢长回答','tentative')]);self.assertEqual(s.profile('a')[0]['value'],'喜欢简短回答')
   s.save_profile('a',0,[self.row('现在喜欢详细解释')]);self.assertEqual(len(s.profile('a')),1);self.assertEqual(s.profile('a')[0]['value'],'现在喜欢详细解释');s.close()
 def test_forget_prevents_inflight_repopulation_and_recall_removes_sources(self):
  s=Store(':memory:');s.save_profile('a',0,[self.row()]);s.forget_profile('a');self.assertFalse(s.save_profile('a',0,[self.row()]));self.assertEqual(s.profile('a'),[])
  s.save_profile('a',1,[self.row()]);s.recall('a','1');self.assertEqual(s.profile('a'),[]);s.save_profile('a',1,[self.row()]);self.assertEqual(s.profile('a'),[]);s.close()
 def test_evidence_is_current_user_text_and_test_secrets_are_excluded(self):
  row=dict(key='风格',value='简短',kind='preference',confidence='explicit',evidence='我喜欢短回答')
  items=[dict(content='我喜欢短回答，请一直这样。',onebot_message_id='1')]
  self.assertEqual(len(validate_updates({'updates':[row]},items)),1)
  self.assertEqual(validate_updates({'updates':[row]},[dict(content='不是这段话',onebot_message_id='2')]),[])
  row.update(value='测试暗号青柠842');self.assertEqual(validate_updates({'updates':[row]},items),[])
 def test_bounded_profile_and_injection_budget(self):
  s=Store(':memory:')
  for i in range(50):s.save_profile('a',0,[self.row()|dict(key=str(i))])
  self.assertEqual(len(s.profile('a')),32);self.assertLessEqual(len(json.dumps(profile_context(s.profile('a'),'怎么说',500),ensure_ascii=False).encode()),500);s.close()

class BackgroundProfileTests(unittest.IsolatedAsyncioTestCase):
 async def test_reply_path_is_not_blocked_and_forget_wins_async_race(self):
  s=Store(':memory:');gate=asyncio.Event()
  async def turn(*args):
   await gate.wait()
   return ModelReply(text=json.dumps({'updates':[dict(key='风格',value='简短回答',kind='preference',confidence='explicit',evidence='我平时喜欢简短回答')]}))
  app=NS(store=s,cfg=NS(models={'m':object()}),llm=NS(turn=turn));p=ProfileMemory(app)
  p.observe('a',[dict(content='我平时喜欢简短回答，先说结论。',onebot_message_id='1')],'m')
  self.assertEqual(s.profile('a'),[]);await asyncio.sleep(0);s.forget_profile('a');gate.set();await p.flush('a');self.assertEqual(s.profile('a'),[])
  p.observe('a',[dict(content='我平时喜欢简短回答，先说结论。',onebot_message_id='2')],'m');await p.flush('a');self.assertTrue(s.profile('a'));await p.close();s.close()
