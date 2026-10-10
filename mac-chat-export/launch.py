#!/usr/bin/env python3
"""Start the pinned QCE engine and archive only its completed export tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
ENGINE = RUNTIME / "NapCat-QCE-macOS-arm64"
DIGEST = "a241a2a694edb317fa494e9f9a0bda79284937be97afa7bfea269741b7f15a9a"


def check():
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise ValueError("本版读取组件需要 Apple 芯片 Mac。")
    archive = RUNTIME / "qce-v6.3.2.tar.gz"
    if not archive.is_file():
        raise ValueError("缺少 QCE v6.3.2 官方发布包，请按说明准备运行组件。")
    with archive.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != DIGEST:
            raise ValueError("运行组件校验失败；请重新下载。")
    if not (ENGINE / "launcher-user.sh").is_file():
        raise ValueError("运行组件尚未解压。")
    if not Path("/Applications/QQ.app").is_dir():
        raise ValueError("请先安装官方 Mac 版 QQ。")
    for port in (40683, 40684):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except PermissionError:
                raise ValueError("当前运行环境禁止检查本机端口；请在 Mac 终端直接运行环境检查。") from None
            except OSError:
                raise ValueError(f"端口 {port} 已被占用。请先关闭本工具的上一次运行。") from None


def security_file():
    directory = RUNTIME / "security"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "security.json"
    if not path.exists():
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        config = {"accessToken": "".join(secrets.choice(alphabet) for _ in range(40)),
                  "secretKey": "".join(secrets.choice(alphabet) for _ in range(64)),
                  "allowedIPs": ["127.0.0.1", "::1"], "tokenExpired": None,
                  "serverHost": "127.0.0.1", "disableIpWhitelist": False}
        with path.open("x", encoding="utf-8") as handle:
            json.dump(config, handle)
        path.chmod(0o600)
    return path


def stop_process(process):
    if process is None:
        return
    # Each child has a new session: only processes owned by this launch are stopped.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--accept", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        check()
        if args.check:
            print("环境检查通过：Apple 芯片、QQ、QCE v6.3.2 校验和本机端口。未启动或登录 QQ。")
            return
        if not args.accept:
            print("首次运行说明：本工具使用非官方开源 QCE 读取你的 QQ 聊天记录。\n"
                  "它会创建专用 QQ 副本并重新签名；副本失去 App Sandbox 保护。\n"
                  "副本与日常 QQ 共用聊天数据库，并占用电脑端登录名额。\n"
                  "请先完全退出桌面 QQ；原始 QQ.app 不会被修改。\n"
                  "请选择是否继续启动，随后用需要导出的个人 QQ 扫码。")
            if input("继续请输入 YES，其他输入退出：").strip() != "YES":
                print("未启动。")
                return
        configuration = security_file()
        print("\n扫码后在导出页面选择好友或群聊、时间范围，格式选 JSON，开启图片下载。\n"
              f"完成任务后自动整理到：{ROOT / 'output'}\n"
              "按 Control+C 可停止本次读取。不要同时启动日常桌面 QQ。\n", flush=True)
        env = dict(os.environ)
        env.update({"QCE_SERVER_PORT": "40683", "QCE_BRIDGE_PORT": "40684",
                    "QCE_CONFIG_DIR": str(configuration.parent)})
        watcher = None
        engine = None
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            watcher = subprocess.Popen([sys.executable, str(ROOT / "archive.py"), "--watch",
                                        "--security", str(configuration), "--output",
                                        str(ROOT / "output"), "--port", "40683"],
                                       start_new_session=True)
            engine = subprocess.Popen(["/bin/bash", str(ENGINE / "launcher-user.sh")],
                                      cwd=ENGINE, env=env, start_new_session=True)
            while engine.poll() is None:
                if watcher.poll() is not None:
                    raise ValueError("自动整理程序已停止，请重新启动工具。")
                time.sleep(0.5)
            if engine.returncode:
                raise ValueError("QQ 读取组件启动失败，请查看上方提示（例如桌面 QQ 尚未退出）。")
        finally:
            stop_process(engine)
            stop_process(watcher)
    except KeyboardInterrupt:
        print("\n本次读取已停止。已生成的归档保留。")
    except (OSError, ValueError) as exc:
        print(f"无法启动：{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
