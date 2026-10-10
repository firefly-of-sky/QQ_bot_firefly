"""Create local configuration without overwriting existing files or printing secrets."""
import os
from pathlib import Path
import secrets


def initialize(root: Path):
    for name, template in ((".env", ".env.example"), ("config.toml", "config.example.toml")):
        target = root / name
        if target.exists():
            print(f"保留已有 {name}")
            continue
        content = (root / template).read_text(encoding="utf-8")
        if name == ".env":
            content = content.replace("replace-with-a-random-token", secrets.token_urlsafe(32))
            content = content.replace("replace-with-another-random-token", secrets.token_urlsafe(32))
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"保留已有 {name}")
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        print(f"已创建 {name}")
    print("请在 .env 填写主人 QQ 号和模型密钥，在 config.toml 填写 API 地址和模型名。")


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1])
