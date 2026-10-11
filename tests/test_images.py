import base64
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from qqbot.bot import Bot
from qqbot.images import ImageError, ImageLoader, image_data_url, parse_message, validate_url
from qqbot.llm import messages
from qqbot.store import Store
from test_bot import config, event

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
IMAGE_URL = 'https://gchat.qpic.cn/example'


def picture(url=IMAGE_URL):
    return {"type": "image", "data": {"url": url, "file": "image.png"}}


class ParseTests(unittest.TestCase):
    def test_image_only_mixed_and_multiple(self):
        self.assertEqual(validate_url('https://multimedia.nt.qq.com.cn/image'), 'https://multimedia.nt.qq.com.cn/image')
        self.assertEqual(parse_message([picture()], 4), ('', [IMAGE_URL]))
        text, images = parse_message([picture(), {"type": "text", "data": {"text": "比较图片"}}, picture()], 4)
        self.assertEqual(text, '比较图片')
        self.assertEqual(images, [IMAGE_URL, IMAGE_URL])
        self.assertEqual(parse_message([{"type": "image", "data": {"file": IMAGE_URL}}], 4)[1], [IMAGE_URL])

    def test_url_limits_and_unsupported(self):
        for url in ['file:///etc/passwd', 'http://localhost/a', 'http://127.0.0.1/a',
                    'https://qpic.cn.evil.test/a', 'https://qpic.cn@evil.test/a',
                    'https://evil.test/a', 'https://gchat.qpic.cn:123/a']:
            with self.subTest(url=url), self.assertRaises(ImageError):
                validate_url(url)
        for msg in [[picture()] * 5, [{"type": "record", "data": {}}],
                    [{"type": "image", "data": {"file": "local.png"}}],
                    [{"type": "image", "data": {"url": IMAGE_URL, "type": "flash"}}]]:
            with self.assertRaises(ImageError):
                parse_message(msg, 4)

    def test_formats_and_multimodal_payload(self):
        data = image_data_url(PNG)
        self.assertEqual(base64.b64decode(data.split(',')[1]), PNG)
        self.assertTrue(image_data_url(b'\xff\xd8\xffrest').startswith('data:image/jpeg'))
        self.assertTrue(image_data_url(b'RIFF0000WEBPrest').startswith('data:image/webp'))
        for bad in [b'', b'<html>error</html>', b'GIF89a']:
            with self.assertRaises(ImageError):
                image_data_url(bad)
        payload = messages(config(), [('before', 'answer')], '看图', [data])
        self.assertEqual(payload[-1]['content'], [
            {'type': 'text', 'text': '看图'}, {'type': 'image_url', 'image_url': {'url': data}}])
        self.assertEqual(payload[1]['content'], 'before')


class ImageBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.llm, self.transport, self.loader = AsyncMock(), AsyncMock(), AsyncMock()
        self.llm.complete.return_value = '图中有一只猫。'
        self.loader.load.return_value = [image_data_url(PNG)]
        self.bot = Bot(config(), self.store, self.llm, self.transport, self.loader)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_image_only_and_followup_history_without_image_secrets(self):
        await self.bot.handle(event(message=[picture()]))
        self.llm.complete.assert_not_awaited()
        await self.bot.handle(event(10, '请描述图片'))
        content = self.llm.complete.call_args.args[1][-1]['content']
        self.assertIn('描述图片', content[0]['text'])
        self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
        history = self.store.history('private:456:123', 20)
        self.assertIn('原图未保留', history[0][0])
        self.assertNotIn('qpic.cn', str(history))
        self.assertNotIn('base64', str(history))
        await self.bot.handle(event(2, '这只猫是什么颜色？'))
        self.assertEqual(self.llm.complete.call_args.args[1][2]['content'], '图中有一只猫。')
        self.loader.load.assert_awaited_once()

    async def test_image_caption_and_model_switch(self):
        await self.bot.handle(event(message=[{"type": "text", "data": {"text": "读出文字"}}, picture()]))
        self.assertEqual(self.llm.complete.call_args.args[1][-1]['content'][0]['text'], '读出文字')
        await self.bot.handle(event(2, '/model second'))
        self.assertEqual(self.store.history('private:456:123', 20), [])

    async def test_failed_download_does_not_call_model_or_persist(self):
        self.loader.load.side_effect = ImageError('QQ 图片下载超时，请稍后重新发送。')
        await self.bot.handle(event(message=[picture()]))
        await self.bot.handle(event(10, '看图'))
        self.llm.complete.assert_not_awaited()
        self.assertEqual(self.store.history('private:456:123', 20), [])
        self.assertIn('超时', self.transport.send.call_args.args[1])

    async def test_text_only_model_and_unauthorized_do_not_download(self):
        models = dict(self.bot.cfg.models)
        models['first'] = replace(models['first'], supports_images=False)
        self.bot.cfg = replace(self.bot.cfg, models=models)
        await self.bot.handle(event(message=[picture()]))
        await self.bot.handle(event(2, message=[picture()], user_id=999, sub_type="other"))
        self.loader.load.assert_not_awaited()
        self.llm.complete.assert_not_awaited()


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def response(self, data, length=None, status=200):
        response = AsyncMock()
        response.status = status
        response.content_length = length
        response.content = MagicMock()
        async def chunks(*args):
            for part in data:
                yield part
        response.content.iter_chunked = chunks
        http = MagicMock()
        http.get.return_value.__aenter__ = AsyncMock(return_value=response)
        http.get.return_value.__aexit__ = AsyncMock(return_value=False)
        return http

    async def test_download_and_size_limits(self):
        loader = ImageLoader(replace(config(), max_image_bytes=len(PNG)))
        http = self.response([PNG[:20], PNG[20:]])
        self.assertEqual(await loader.download(http, IMAGE_URL), image_data_url(PNG))
        http.get.assert_called_once_with(IMAGE_URL, allow_redirects=False)
        for http in [self.response([PNG], len(PNG)+1), self.response([PNG, b'x']),
                     self.response([PNG], status=302), self.response([PNG], status=404),
                     self.response([b'not an image'])]:
            with self.assertRaises(ImageError):
                await loader.download(http, IMAGE_URL)

    async def test_network_failure_and_timeout(self):
        for failure in (TimeoutError(), aiohttp.ClientConnectionError()):
            http = MagicMock()
            http.get.side_effect = failure
            with self.assertRaises(ImageError):
                await ImageLoader(config()).download(http, IMAGE_URL)
