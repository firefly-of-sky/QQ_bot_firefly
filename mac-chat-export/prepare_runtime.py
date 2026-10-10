#!/usr/bin/env python3
"""Download and verify the fixed upstream release; never start its code."""
import hashlib
from pathlib import Path
import tarfile
from urllib.request import urlopen

from launch import DIGEST, RUNTIME

URL = "https://github.com/shuakami/qq-chat-exporter/releases/download/v6.3.2/NapCat-QCE-macOS-arm64-v6.3.2.tar.gz"


def main():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    archive = RUNTIME / "qce-v6.3.2.tar.gz"
    if not archive.exists():
        partial = archive.with_suffix(".download")
        with urlopen(URL, timeout=60) as response, partial.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
        partial.rename(archive)
    with archive.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != DIGEST:
            raise ValueError("文件校验失败，未解压。请重新下载官方 v6.3.2 发布包。")
    with tarfile.open(archive) as pack:
        # The pinned release has files and directories only. Reject paths or
        # links that could redirect writes outside the isolated runtime folder.
        for member in pack.getmembers():
            target = (RUNTIME / member.name).resolve()
            if not target.is_relative_to(RUNTIME.resolve()) or not (member.isfile() or member.isdir()):
                raise ValueError("发布包包含不安全的路径或链接，未解压。")
        pack.extractall(RUNTIME, filter="data")
    print("QCE v6.3.2 已准备并校验。尚未启动 QQ 或读取聊天。")


if __name__ == "__main__":
    main()
