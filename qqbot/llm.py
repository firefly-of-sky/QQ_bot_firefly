import asyncio
import os

import aiohttp

from .config import Config, Model


class ModelError(Exception):
    pass


def messages(cfg: Config, history: list[tuple[str, str]], text: str) -> list[dict]:
    def cost(value: str) -> int:
        return len(value.encode("utf-8")) + 32
    used = cost(cfg.system_prompt) + cost(text)
    if used > cfg.context_budget_bytes:
        raise ModelError("消息超过当前上下文预算，请缩短内容。")
    selected = []
    for user, assistant in reversed(history[-cfg.history_turns:]):
        size = cost(user) + cost(assistant)
        if used + size > cfg.context_budget_bytes:
            break
        selected.append((user, assistant))
        used += size
    result = [{"role": "system", "content": cfg.system_prompt}]
    for user, assistant in reversed(selected):
        result.extend([{"role": "user", "content": user}, {"role": "assistant", "content": assistant}])
    result.append({"role": "user", "content": text})
    return result


class LLM:
    def __init__(self, http: aiohttp.ClientSession, cfg: Config):
        self.http, self.cfg = http, cfg

    async def complete(self, model: Model, conversation: list[dict]) -> str:
        try:
            async with self.http.post(
                model.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + os.environ[model.api_key_env]},
                json={"model": model.model, "messages": conversation, "stream": False,
                      "max_tokens": self.cfg.max_output_tokens},
                timeout=aiohttp.ClientTimeout(total=self.cfg.request_timeout_seconds),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise ModelError("模型鉴权失败，请检查 API 密钥和权限。")
                if response.status == 429:
                    raise ModelError("模型请求受限，请稍后重试或检查额度。")
                if response.status != 200:
                    raise ModelError(f"模型服务请求失败（HTTP {response.status}），请检查配置或稍后重试。")
                data = await response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("empty response")
                return content.strip()
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise ModelError("模型回复超时，请稍后重试。") from exc
        except aiohttp.ClientConnectionError as exc:
            raise ModelError("无法连接模型服务，请检查网络。") from exc
        except (aiohttp.ClientError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("模型返回格式异常，请检查接口兼容性。") from exc
