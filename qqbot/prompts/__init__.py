"""Owner-controlled persona files, snapshotted at the start of each request."""
from pathlib import Path


def load_persona(filename: str = "") -> str:
    path = Path(filename) if filename else Path(__file__).with_name("assistant.md")
    try:
        with path.open("rb") as source:
            data = source.read(16385)
        if len(data) > 16384:
            raise ValueError("人格文件不能超过 16 KiB")
        text = data.decode("utf-8-sig").strip()
        if not text:
            raise ValueError("人格文件不能为空")
        return text
    except (OSError, UnicodeError) as exc:
        raise ValueError("无法读取人格文件，请检查 UTF-8 编码及 persona_file 路径") from exc
