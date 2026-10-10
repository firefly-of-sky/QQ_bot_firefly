#!/usr/bin/env python3
"""Convert QCE v6.3.2 JSON / chunked JSONL to portable chat archives.

No model API, remote media fetching, or direct access to QQ databases.
The upstream QCE engine owns collection; this module owns presentation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler
import zipfile

VERSION = "0.2.0"


def safe_name(value: str) -> str:
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", value).strip(" .")
    return value[:80] or "chat"


def within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def load_source(path: Path):
    path = path.resolve()
    if path.is_dir():
        path = path / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("请选择 QCE 导出的 JSON 或分块 JSONL 的 manifest.json。")
    if isinstance(data.get("messages"), list):
        return path, data, iter(data["messages"])
    chunks = data.get("chunked", {}).get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("文件中没有 QCE 消息列表；请在导出时选择 JSON 格式。")

    def messages():
        for chunk in chunks:
            rel = chunk.get("relativePath")
            if not isinstance(rel, str):
                raise ValueError("分块记录缺少路径。")
            target = (path.parent / rel).resolve()
            if not within(target, path.parent):
                raise ValueError("分块路径越出了导出目录。")
            actual = 0
            with target.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        actual += 1
                        yield json.loads(line)
            if chunk.get("count") is not None and actual != chunk["count"]:
                raise ValueError("分块消息数量不一致，原始导出可能不完整。")

    return path, data, messages()


class Renderer:
    def __init__(self, source: Path, destination: Path):
        self.source = source
        self.destination = destination
        self.images = 0
        self.missing = []
        self.copied: dict[str, str] = {}
        self.nested_messages = 0
        self.extra_elements = 0

    def media_path(self, data: dict) -> Path | None:
        # QCE paths are usually "images/name.jpg" while copies live in
        # "resources/images/name.jpg". Never archive arbitrary external paths.
        candidates = []
        raw = data.get("localPath")
        if isinstance(raw, str) and raw and not re.match(r"^[a-z]+://", raw):
            original = Path(raw)
            if original.is_absolute():
                candidates.append(original)
            else:
                candidates += [self.source / original, self.source / "resources" / original]
        name = data.get("filename") or data.get("fileName")
        if isinstance(name, str) and Path(name).name == name:
            candidates += [self.source / "resources" / "images" / name,
                           self.source / "images" / name]
        for candidate in candidates:
            if within(candidate, self.source) and candidate.is_file():
                return candidate
        return None

    def image(self, data: dict, message_id: str) -> str:
        path = self.media_path(data)
        if path is None:
            self.missing.append({"messageId": message_id, "filename": data.get("filename")
                                 or data.get("fileName") or "", "reason": "导出目录内未找到图片文件"})
            return "[图片缺失：未取回图片文件]"
        with path.open("rb") as handle:
            first = handle.read(32)
            handle.seek(0)
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest in self.copied:
            return f"[图片: {self.copied[digest]}]"
        # Use the actual format, not a misleading filename suffix.
        if first.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif first.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif first.startswith((b"GIF87a", b"GIF89a")):
            extension = ".gif"
        elif first[:4] == b"RIFF" and first[8:12] == b"WEBP":
            extension = ".webp"
        elif first.startswith(b"BM"):
            extension = ".bmp"
        elif first.startswith((b"II*\x00", b"MM\x00*")):
            extension = ".tiff"
        elif first[4:8] == b"ftyp" and first[8:12] in (b"heic", b"heix", b"mif1"):
            extension = ".heic"
        else:
            self.missing.append({"messageId": message_id, "filename": path.name,
                                 "reason": "文件内容不是已支持的图片格式"})
            return "[图片缺失：图片文件格式无法确认]"
        self.images += 1
        rel = f"images/{self.images:03d}{extension}"
        shutil.copyfile(path, self.destination / rel)
        self.copied[digest] = rel
        return f"[图片: {rel}]"

    def message(self, message: dict, depth: int = 0) -> str:
        if not isinstance(message, dict):
            raise ValueError("消息结构异常。")
        sender = message.get("sender") or {}
        name = sender.get("name") or sender.get("groupCard") or sender.get("nickname") \
            or sender.get("uin") or sender.get("uid") or "[发送者未知]"
        stamp = message.get("time") or "[时间未知]"
        content = message.get("content") or {}
        msg_id = str(message.get("id", ""))
        elements = content.get("elements") or []
        parts = []
        media_seen = []
        has_text = False
        for element in elements:
            kind = element.get("type", "unknown")
            data = element.get("data") or {}
            if not isinstance(data, dict):
                data = {"value": data}
            if kind == "text":
                parts.append(str(data.get("text", "")))
                has_text = True
            elif kind == "image":
                parts.append(self.image(data, msg_id))
                media_seen.append(data)
            elif kind == "at":
                parts.append("@" + str(data.get("name") or data.get("uid") or "[未知]"))
                has_text = True
            elif kind == "forward" and isinstance(data.get("messages"), list) and data["messages"]:
                if depth >= 20:
                    raise ValueError("嵌套转发层数过多；请分段导出。")
                parts.append("\n[合并转发开始]\n")
                for inner in data["messages"]:
                    self.nested_messages += 1
                    parts.append(self.message(inner, depth + 1))
                parts.append("[合并转发结束]\n")
            elif kind == "reply":
                parts.append("\n[引用: " + str(data.get("senderName") or "") + " "
                             + str(data.get("text") or data.get("content") or data.get("messageId")
                                   or data.get("id") or "原消息不可见") + "]\n")
            else:
                # Retain all unrendered fields rather than silently drop messages.
                self.extra_elements += 1
                labels = {"audio": "语音", "video": "视频", "file": "文件", "face": "表情",
                          "market_face": "表情", "json": "卡片", "location": "位置",
                          "system": "系统", "forward": "合并转发"}
                parts.append("\n[" + labels.get(kind, kind) + ": "
                             + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "]\n")
        if not has_text and content.get("text") and not elements:
            parts.append(str(content["text"]))
        for resource in content.get("resources") or []:
            if resource.get("type") != "image":
                continue
            # Avoid counting the same resource twice; do not match solely by list index.
            identifiers = {str(resource.get(k)) for k in ("filename", "fileName", "localPath", "url")
                           if resource.get(k)}
            represented = any(identifiers.intersection(str(e.get(k)) for k in
                              ("filename", "fileName", "localPath", "url") if e.get(k))
                              or e == resource for e in media_seen)
            if not represented:
                parts.append(self.image(resource, msg_id))
        if message.get("recalled"):
            parts.insert(0, "[已撤回]\n")
        if message.get("system"):
            parts.insert(0, "[系统消息]\n")
        body = "".join(parts).strip() or "[无可见内容]"
        return f"{stamp} {name}\n{body}\n\n"


def archive(source: Path, output: Path, task: dict | None = None) -> dict:
    source, document, messages = load_source(source)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    chat = document.get("chatInfo") or {}
    peer = chat.get("peerUin") or chat.get("peerUid") or chat.get("type") or "chat"
    stem = safe_name(str(chat.get("name") or source.stem)) + "_" + safe_name(str(peer))
    batch = output / (stem + "_" + time.strftime("%Y%m%d_%H%M%S"))
    suffix = 1
    while batch.exists() or Path(str(batch) + ".zip").exists():
        batch = output / (stem + "_" + time.strftime("%Y%m%d_%H%M%S") + f"_{suffix}")
        suffix += 1
    staging = Path(tempfile.mkdtemp(prefix=".archive-", dir=output))
    count = 0
    try:
        dest = staging / "data" / safe_name(stem)
        (dest / "images").mkdir(parents=True)
        renderer = Renderer(source.parent, dest)
        first_time = None
        last_time = None
        with (dest / "chat.txt").open("w", encoding="utf-8", newline="\n") as handle:
            for message in messages:
                handle.write(renderer.message(message))
                count += 1
                first_time = first_time or message.get("time")
                last_time = message.get("time") or last_time
        expected = document.get("statistics", {}).get("totalMessages")
        if expected is not None and int(expected) != count:
            raise ValueError(f"消息数量不一致（读取 {count}，原导出声明 {expected}），未发布结果。")
        report = {"version": VERSION, "messages": count, "nestedMessages": renderer.nested_messages,
                  "images": renderer.images, "missingImages": renderer.missing,
                  "otherElements": renderer.extra_elements, "firstTime": first_time,
                  "lastTime": last_time, "source": source.name,
                  "completeness": "仅核对本次 QCE 导出的消息，不代表 QQ 的全部历史。",
                  "taskResourceSummary": (task or {}).get("resourceSummary"),
                  "roamingScan": (task or {}).get("roamingScan")}
        (staging / "整理报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        os.rename(staging, batch)
        zip_path = Path(str(batch) + ".zip")
        tmp_zip = output / ("." + zip_path.name + ".tmp")
        try:
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                for file in sorted(batch.rglob("*")):
                    zipped.write(file, file.relative_to(batch))
            os.rename(tmp_zip, zip_path)
        finally:
            tmp_zip.unlink(missing_ok=True)
        return {**report, "folder": str(batch), "zip": str(zip_path)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def process_tasks(tasks: list[dict], output: Path, state: dict,
                  errors: list[str] | None = None) -> list[dict]:
    results = []
    for task in tasks:
        if task.get("status") != "completed" or str(task.get("format", "")).upper() != "JSON":
            continue
        task_id = str(task.get("id") or task.get("taskId") or "")
        if not task_id or task_id in state:
            continue
        raw = task.get("filePath")
        if task.get("isZipExport") and task.get("originalFilePath"):
            raw = task["originalFilePath"]
        if not isinstance(raw, str) or not raw:
            continue
        try:
            result = archive(Path(raw), output, task)
        except (OSError, ValueError, KeyError, TypeError):
            if errors is not None:
                errors.append(task_id)
            continue
        state[task_id] = {"folder": result["folder"], "zip": result["zip"]}
        results.append(result)
    return results


def watch(security: Path, output: Path, port: int):
    output.mkdir(parents=True, exist_ok=True)
    state_file = output / ".processed-tasks.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    opener = build_opener(ProxyHandler({}))
    connected = False
    failed = False
    last_errors: set[str] = set()
    print("自动整理已就绪：在 QQ 导出页面选择 JSON，并开启图片下载。", flush=True)
    while True:
        try:
            token = json.loads(security.read_text())["accessToken"]
            request = Request(f"http://127.0.0.1:{port}/api/tasks",
                              headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=5) as response:
                payload = json.load(response)
            if not payload.get("success"):
                raise ValueError("本机导出服务尚未就绪或登录已失效。")
            if not connected:
                print("已连接本机导出服务。完成的 JSON 任务将自动生成目标文件夹和 ZIP。", flush=True)
                connected = True
            errors: list[str] = []
            results = process_tasks(payload.get("data", {}).get("tasks", []), output, state, errors)
            if set(errors) - last_errors:
                print("部分任务尚无法整理：请保留原始 JSON 和资源文件，确认消息数量一致。"
                      "其他任务将继续处理。", flush=True)
            last_errors = set(errors)
            if results:
                temp = state_file.with_suffix(".tmp")
                temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                os.replace(temp, state_file)
            for result in results:
                print(f"整理完成：{result['messages']} 条消息，{result['images']} 张图片，"
                      f"{len(result['missingImages'])} 个缺失图片引用。\n位置：{result['folder']}", flush=True)
            failed = False
        except (OSError, ValueError, KeyError, TypeError):
            if connected and not failed:
                print("暂时无法整理：请确认导出服务仍在运行、JSON 文件与图片未被移动。"
                      "已有结果保留；恢复后会重试。", flush=True)
                failed = True
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="QQ 聊天整理：QCE JSON → chat.txt + images + ZIP")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--security", type=Path)
    parser.add_argument("--port", type=int, default=40683)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.watch:
            if args.security is None:
                parser.error("需要指定本机导出服务的凭证文件。")
            watch(args.security, args.output, args.port)
        elif args.source:
            print(json.dumps(archive(args.source, args.output), ensure_ascii=False, indent=2))
        else:
            parser.error("请选择 QCE JSON 文件，或使用 --watch 自动整理。")
    except KeyboardInterrupt:
        print("自动整理已停止。")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"整理失败：{exc}\n")


if __name__ == "__main__":
    main()
