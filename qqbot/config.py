import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Model:
    base_url: str
    model: str
    api_key_env: str
    supports_images: bool = True


@dataclass(frozen=True)
class Config:
    owner: int
    token: str
    ws_url: str
    database: str
    models: dict[str, Model]
    default_model: str
    system_prompt: str = "你是一位友善、准确的中文助手。"
    history_turns: int = 20
    max_input_chars: int = 8000
    context_budget_bytes: int = 48000
    max_output_tokens: int = 2048
    reply_chunk_chars: int = 1500
    request_timeout_seconds: float = 90
    max_images: int = 4
    max_image_bytes: int = 5 * 1024 * 1024
    image_timeout_seconds: float = 30
    max_concurrent_requests: int = 3
    requests_per_minute: int = 10
    image_wait_seconds: float = 3
    media_ttl_seconds: float = 600
    progress_delay_seconds: float = 12
    search_api_key_env: str = "TAVILY_API_KEY"
    auto_search: bool = True
    search_max_queries: int = 3
    search_max_results: int = 9
    search_timeout_seconds: float = 45
    progress_templates: dict[str, list[str]] = field(default_factory=dict)


def load() -> Config:
    with Path(os.environ.get("CONFIG_PATH", "config.toml")).open("rb") as f:
        raw = tomllib.load(f)
    model_values = raw.pop("models")
    if not isinstance(model_values, dict):
        raise ValueError("models 必须是 TOML 表")
    models = {alias: Model(**value) for alias, value in model_values.items()}
    cfg = Config(owner=int(os.environ["OWNER_QQ"]), token=os.environ["ONEBOT_TOKEN"],
                 ws_url=os.environ.get("ONEBOT_URL", "ws://localhost:3001"),
                 database=os.environ.get("DATABASE_PATH", "data/chat.sqlite3"),
                 models=models, **raw)
    if cfg.owner <= 0 or not cfg.token or cfg.token.startswith("replace-"):
        raise ValueError("OWNER_QQ 或 ONEBOT_TOKEN 尚未正确配置")
    if cfg.default_model not in models:
        raise ValueError("default_model 不在 models 中")
    for field in ("history_turns", "max_input_chars", "context_budget_bytes", "max_output_tokens", "reply_chunk_chars", "max_images", "max_image_bytes", "max_concurrent_requests", "requests_per_minute", "search_max_queries", "search_max_results"):
        value = getattr(cfg, field)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field} 必须是正整数")
    if (type(cfg.request_timeout_seconds) not in (int, float)
            or not math.isfinite(cfg.request_timeout_seconds) or cfg.request_timeout_seconds <= 0):
        raise ValueError("request_timeout_seconds 必须为有限正数")
    if not isinstance(cfg.system_prompt, str):
        raise ValueError("system_prompt 必须为文字")
    for field in ("image_wait_seconds", "media_ttl_seconds", "progress_delay_seconds", "search_timeout_seconds"):
        value = getattr(cfg, field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field} 必须为有限正数")
    from .progress import DEFAULT_TEMPLATES
    if not isinstance(cfg.progress_templates, dict):
        raise ValueError("progress_templates 必须为表")
    for stage, options in cfg.progress_templates.items():
        if (stage not in DEFAULT_TEMPLATES or not isinstance(options, list) or not options
                or any(not isinstance(text, str) or not text.strip() or len(text) > 200 for text in options)):
            raise ValueError("等待提示须使用已知阶段和非空短句列表")
    if cfg.search_max_queries > 3 or cfg.search_max_results > 12:
        raise ValueError("搜索最多允许 3 次查询、12 条结果")
    if cfg.media_ttl_seconds <= cfg.image_wait_seconds:
        raise ValueError("media_ttl_seconds 必须大于 image_wait_seconds")
    if type(cfg.auto_search) is not bool or not isinstance(cfg.search_api_key_env, str):
        raise ValueError("搜索配置无效")
    if (type(cfg.image_timeout_seconds) not in (int, float)
            or not math.isfinite(cfg.image_timeout_seconds) or cfg.image_timeout_seconds <= 0):
        raise ValueError("image_timeout_seconds 必须为有限正数")
    ws = urlsplit(cfg.ws_url)
    if ws.scheme not in ("ws", "wss") or not ws.hostname or ws.username or ws.fragment:
        raise ValueError("ONEBOT_URL 必须为 ws/wss 地址")
    for alias, model in models.items():
        if type(model.supports_images) is not bool:
            raise ValueError("supports_images 必须为布尔值")
        if not all(isinstance(value, str) and value for value in (model.base_url, model.model, model.api_key_env)):
            raise ValueError("模型配置必须为非空文字")
        parsed = urlsplit(model.base_url)
        if not alias or any(c.isspace() for c in alias) or not model.model:
            raise ValueError("模型别名不能包含空白，模型名不能为空")
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
            raise ValueError(f"模型 {alias} 的 base_url 无效")
        if not os.environ.get(model.api_key_env) or os.environ[model.api_key_env].startswith("replace-"):
            raise ValueError(f"请填写模型 {alias} 对应的环境变量 {model.api_key_env}")
    return cfg
