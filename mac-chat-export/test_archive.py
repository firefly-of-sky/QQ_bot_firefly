import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("archive", Path(__file__).with_name("archive.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# Actual tiny PNG fixture; only content bytes are used by the archival code.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=")


def msg(identifier, elements, time="2026-10-10 10:30:00"):
    return {"id": identifier, "time": time, "timestamp": 1791599400000,
            "sender": {"name": "张三", "uin": "123"},
            "content": {"elements": elements, "resources": []}}


def element(kind, **data):
    return {"type": kind, "data": data}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "raw"
        self.source.mkdir()
        self.output = self.root / "output"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, messages, **extra):
        path = self.source / "export.json"
        path.write_text(json.dumps({"chatInfo": {"name": "同学.群/聊", "peerUid": "g1"},
                                    "statistics": {"totalMessages": len(messages)},
                                    "messages": messages, **extra}, ensure_ascii=False))
        return path

    def image(self, rel="resources/images/a.jpg", contents=PNG):
        path = self.source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def test_order_actual_image_format_and_portable_zip(self):
        self.image()
        data = {"localPath": "images/a.jpg", "filename": "a.jpg"}
        first = msg("1", [element("text", text="图前"), element("image", **data),
                          element("text", text="图后")])
        first["content"]["resources"] = [{"type": "image", **data}]
        second = msg("2", [element("text", text="第二条")])
        result = module.archive(self.write([first, second]), self.output)
        folder = Path(result["folder"])
        chat = next(folder.rglob("chat.txt")).read_text()
        self.assertIn("图前[图片: images/001.png]图后", chat)
        self.assertLess(chat.index("图后"), chat.index("第二条"))
        self.assertEqual(result["images"], 1)
        with zipfile.ZipFile(result["zip"]) as archive:
            self.assertTrue(any(x.endswith("/images/001.png") for x in archive.namelist()))
            self.assertTrue(all(not x.startswith("/") for x in archive.namelist()))
        self.assertEqual(next(folder.rglob("001.png")).read_bytes(), PNG)

    def test_duplicate_content_and_filename_collisions(self):
        self.image("left/same.png")
        self.image("right/same.png", PNG + b"different bytes")
        messages = [msg("1", [element("image", localPath="left/same.png")]),
                    msg("2", [element("image", localPath="right/same.png")]),
                    msg("3", [element("image", localPath="left/same.png")])]
        result = module.archive(self.write(messages), self.output)
        self.assertEqual(result["images"], 2)
        chat = next(Path(result["folder"]).rglob("chat.txt")).read_text()
        self.assertEqual(chat.count("images/001.png"), 2)
        self.assertEqual(chat.count("images/002.png"), 1)

    def test_missing_image_and_external_path_are_not_silently_copied(self):
        outside = self.root / "private.png"
        outside.write_bytes(PNG)
        (self.source / "linked.png").symlink_to(outside)
        messages = [msg("1", [element("image", localPath=str(outside)),
                              element("image", localPath="linked.png"),
                              element("image", filename="missing.png")])]
        result = module.archive(self.write(messages), self.output)
        self.assertEqual(result["images"], 0)
        self.assertEqual(len(result["missingImages"]), 3)
        with zipfile.ZipFile(result["zip"]) as zipped:
            self.assertTrue(any(name.endswith("/images/") for name in zipped.namelist()))

    def test_chunked_jsonl_and_nested_forward(self):
        self.image()
        nested = msg("inner", [element("text", text="转发正文"),
                                element("image", localPath="images/a.jpg")])
        outer = msg("outer", [element("forward", messages=[nested])])
        chunks = self.source / "chunks"
        chunks.mkdir()
        (chunks / "c000001.jsonl").write_text(json.dumps(outer) + "\n")
        manifest = self.source / "manifest.json"
        manifest.write_text(json.dumps({"chatInfo": {"name": "分块"},
            "statistics": {"totalMessages": 1}, "chunked": {"chunks": [
                {"relativePath": "chunks/c000001.jsonl", "count": 1}]}}))
        result = module.archive(self.source, self.output)
        self.assertEqual(result["messages"], 1)
        self.assertEqual(result["nestedMessages"], 1)
        self.assertEqual(result["images"], 1)
        self.assertIn("转发正文", next(Path(result["folder"]).rglob("chat.txt")).read_text())

    def test_inconsistent_count_leaves_no_published_archive(self):
        path = self.write([msg("1", [])], statistics={"totalMessages": 5})
        with self.assertRaises(ValueError):
            module.archive(path, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_repeated_export_keeps_existing_files(self):
        path = self.write([msg("1", [element("text", text="原文")])])
        one = module.archive(path, self.output)
        two = module.archive(path, self.output)
        self.assertNotEqual(one["folder"], two["folder"])
        self.assertTrue(Path(one["zip"]).is_file())
        self.assertTrue(Path(two["zip"]).is_file())

    def test_completed_tasks_only_and_bad_task_does_not_block_batch(self):
        source = self.write([msg("1", [element("text", text="任务正文")])])
        tasks = [{"id": "pending", "status": "running", "format": "JSON", "filePath": str(source)},
                 {"id": "bad", "status": "completed", "format": "JSON", "filePath": "/missing"},
                 {"id": "good", "status": "completed", "format": "JSON", "filePath": str(source)},
                 {"id": "html", "status": "completed", "format": "HTML", "filePath": str(source)}]
        state, errors = {}, []
        result = module.process_tasks(tasks, self.output, state, errors)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(state), {"good"})
        self.assertEqual(errors, ["bad"])
        self.assertEqual(module.process_tasks(tasks, self.output, state), [])

    def test_chunk_path_traversal_fails_without_archiving_external_content(self):
        (self.root / "outside.jsonl").write_text(json.dumps(msg("private", [])))
        manifest = self.source / "manifest.json"
        manifest.write_text(json.dumps({"chunked": {"chunks": [
            {"relativePath": "../outside.jsonl"}]}}))
        with self.assertRaises(ValueError):
            module.archive(manifest, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_watcher_authentication_and_completed_task_end_to_end(self):
        source = self.write([msg("1", [element("text", text="端到端任务正文")])])
        credential = self.root / "security.json"
        credential.write_text(json.dumps({"accessToken": "fixture-only-token"}))
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get("Authorization")))
                payload = json.dumps({"success": True, "data": {"tasks": [
                    {"id": "integration", "status": "completed", "format": "JSON",
                     "filePath": str(source)}]}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen([sys.executable, str(Path(module.__file__)), "--watch",
            "--security", str(credential), "--output", str(self.output),
            "--port", str(server.server_address[1])], stdout=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if list(self.output.glob("*.zip")):
                    break
                time.sleep(0.05)
            self.assertEqual(len(list(self.output.glob("*.zip"))), 1)
            self.assertEqual(received[0], ("/api/tasks", "Bearer fixture-only-token"))
            state_file = self.output / ".processed-tasks.json"
            self.assertIn("integration", json.loads(state_file.read_text()))
            text = next(self.output.rglob("chat.txt")).read_text()
            self.assertIn("端到端任务正文", text)
        finally:
            process.terminate()
            process.wait(timeout=5)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
