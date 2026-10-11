"""Parse QQ attachments and download bounded image data without model credentials."""
import base64
from urllib.parse import urlsplit

import aiohttp

from .config import Config


class ImageError(Exception):
    pass


def validate_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme in ("https", "http") and not parsed.username
                 and not parsed.password and parsed.port in (None, 80, 443)
                 and any(host == domain or host.endswith("." + domain)
                         for domain in ("qpic.cn", "qq.com", "qq.com.cn", "gtimg.cn")))
    except ValueError:
        valid = False
    if not valid:
        raise ImageError("无法读取此图片地址，请通过 QQ 直接发送原图，不要发送文件路径或链接。")
    return url


def parse_message(message, max_images: int) -> tuple[str, list[str]]:
    if not isinstance(message, list) or not message:
        raise ImageError("目前支持文字和图片，请直接发送文字或图片；暂不支持语音、视频和文件。")
    texts, images = [], []
    for segment in message:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            raise ImageError("消息格式无法识别，请重新发送文字或图片。")
        kind, data = segment.get("type"), segment["data"]
        if kind == "text" and isinstance(data.get("text"), str):
            texts.append(data["text"])
        elif kind == "image":
            if data.get("type") == "flash":
                raise ImageError("暂不支持闪照，请发送普通图片。")
            url = data.get("url") or data.get("file")
            if not isinstance(url, str) or not url:
                raise ImageError("未收到可用图片地址，请重新发送普通图片。")
            images.append(validate_url(url))
            if len(images) > max_images:
                raise ImageError(f"每条消息最多支持 {max_images} 张图片，请分批发送。")
        else:
            raise ImageError("目前支持文字和图片，请直接发送文字或图片；暂不支持语音、视频和文件。")
    return "".join(texts).strip(), images


def image_data_url(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ImageError("暂不支持此图片格式，请发送 JPG、PNG 或 WebP 图片。")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


class ImageLoader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def load(self, urls: list[str]) -> list[str]:
        if len(urls) > self.cfg.max_images:
            raise ImageError(f"每条消息最多支持 {self.cfg.max_images} 张图片。")
        result = []
        # Separate session: never forward model Authorization headers or netrc credentials.
        async with aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                                         timeout=aiohttp.ClientTimeout(total=self.cfg.image_timeout_seconds)) as http:
            for url in urls:
                result.append(await self.download(http, validate_url(url)))
        return result

    async def download(self, http, url: str) -> str:
        try:
            async with http.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ImageError("QQ 图片下载失败或链接已失效，请重新发送图片。")
                limit = self.cfg.max_image_bytes
                if response.content_length is not None and response.content_length > limit:
                    raise ImageError(f"单张图片超过 {limit // 1024} KiB 限制，请压缩后重发。")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ImageError(f"单张图片超过 {limit // 1024} KiB 限制，请压缩后重发。")
                return image_data_url(bytes(data))
        except TimeoutError as exc:
            raise ImageError("QQ 图片下载超时，请稍后重新发送。") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise ImageError("无法下载 QQ 图片，请检查网络后重新发送。") from exc
