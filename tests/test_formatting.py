import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from qqbot.bot import Bot
from qqbot.formatting import to_plain_text
from qqbot.store import Store
from test_bot import config, event


class PlainTextTests(unittest.TestCase):
    def test_headings_and_emphasis(self):
        self.assertEqual(to_plain_text('## 操作步骤\n\n**重点**、*提示*、__注意__。'),
                         '【操作步骤】\n\n重点、提示、注意。')
        self.assertEqual(to_plain_text('标题\n====\n\n~~过时内容~~'), '【标题】\n\n（删除：过时内容）')

    def test_plain_text_line_breaks_and_formulas(self):
        for text in ['你好😊\n这是第二行。\n\n另一段。', 'x * y = 6; file_name.py; C#; #标签',
                     '$a_b + c_d$ 和 $$x_i * y_j$$', r'\(a_b + c_d\) 与 \[x_i * y_j\]',
                     '未闭合 **加粗', '']:
            with self.subTest(text=text):
                self.assertEqual(to_plain_text(text), text)
        self.assertEqual(to_plain_text('第一行  \n第二行'), '第一行\n第二行')

    def test_links_images_references_and_entities(self):
        self.assertEqual(to_plain_text('[**文档**](https://example.com/a?q=1&b=2)'),
                         '文档（https://example.com/a?q=1&b=2）')
        self.assertEqual(to_plain_text('<https://example.com>'), 'https://example.com')
        self.assertEqual(to_plain_text('[文档][doc]\n\n[doc]: https://example.com'), '文档（https://example.com）')
        self.assertEqual(to_plain_text('![截图](https://example.com/a.png)'), '截图（https://example.com/a.png）')
        self.assertEqual(to_plain_text('A &amp; B，\\*字面星号\\*'), 'A & B，*字面星号*')

    def test_code_preserves_whitespace_symbols_and_literals(self):
        code = '    # comment\n    x = a * b ** 2\n\n    s = "**bold** [x](url)"\n    print("<tag>")'
        self.assertEqual(to_plain_text('```python\n' + code + '\n```'), code)
        self.assertEqual(to_plain_text('~~~\n' + code + '\n~~~'), code)
        self.assertEqual(to_plain_text('```python\n# incomplete fence\nx = 1'), '# incomplete fence\nx = 1')
        self.assertEqual(to_plain_text('运行 `a_b * c #tag`，再运行 ``x = `foo` ``。'),
                         '运行 a_b * c #tag，再运行 x = `foo` 。')

    def test_nested_lists_and_quotes(self):
        self.assertEqual(to_plain_text('3. 第一项\n4. 第二项\n   - **内层**\n   - 子项'),
                         '3. 第一项\n4. 第二项\n   • 内层\n   • 子项')
        self.assertEqual(to_plain_text('> **引用**\n> 下一行\n\n---'), '引用：\n  引用\n  下一行\n\n────────')

    def test_table_transposed_to_labeled_items(self):
        value = '| 名称 | 值 |\n| --- | --- |\n| **方案A** | [链接](https://example.com) |\n| B | a\\|b |'
        self.assertEqual(to_plain_text(value),
                         '【第 1 项】\n名称：方案A\n值：链接（https://example.com）\n\n【第 2 项】\n名称：B\n值：a|b')


class FormattingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_format_before_split_but_store_original(self):
        store = Store(':memory:')
        llm, transport = AsyncMock(), AsyncMock()
        answer = '## 标题\n\n**' + '长回答' * 30 + '**\n\n[文档](https://example.com)'
        llm.complete.return_value = answer
        bot = Bot(replace(config(), reply_chunk_chars=17), store, llm, transport)
        try:
            await bot.handle(event())
            chunks = [call.args[1] for call in transport.send.call_args_list]
            self.assertTrue(all(len(c) <= 17 for c in chunks))
            self.assertEqual(''.join(chunks), to_plain_text(answer))
            self.assertEqual(store.history('private:456:123', 20)[0][1], answer)
            await bot.handle(event(2, '继续'))
            self.assertEqual(llm.complete.call_args.args[1][2]['content'], answer)
        finally:
            store.close()

    async def test_commands_bypass_markdown_renderer(self):
        store = Store(':memory:')
        llm, transport = AsyncMock(), AsyncMock()
        cfg = config()
        models = dict(cfg.models)
        models['first'] = replace(models['first'], model='literal_foo_bar')
        bot = Bot(replace(cfg, models=models), store, llm, transport)
        try:
            await bot.handle(event(text='/models'))
            self.assertIn('literal_foo_bar', transport.send.call_args.args[1])
            llm.complete.assert_not_awaited()
        finally:
            store.close()
