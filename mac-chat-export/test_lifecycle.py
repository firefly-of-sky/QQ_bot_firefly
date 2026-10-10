"""Regression checks for terminal close, orphan recovery and process isolation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import launch
import lifecycle

ROOT = Path(__file__).resolve().parent


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError("等待测试进程退出超时")


def alive(pid):
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="],
                            capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


RUNNER = '''
import json, os, sys, types
from pathlib import Path
import launch
from lifecycle import terminal_lifetime
real_popen = launch.subprocess.Popen
children = []
class ReadyProcess:
    def __init__(self, child): self.child = child
    def __getattr__(self, name): return getattr(self.child, name)
    def poll(self):
        if len(children) == 2 and not Path(sys.argv[1]).exists():
            Path(sys.argv[1]).write_text(json.dumps({"controller":os.getpid(), "children":children}))
        return self.child.poll()
def harmless_child(command, **kwargs):
    kwargs.pop("cwd", None)
    child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
    children.append(child.pid)
    return ReadyProcess(child)
launch.subprocess = types.SimpleNamespace(Popen=harmless_child, TimeoutExpired=launch.subprocess.TimeoutExpired)
launch.security_file = lambda: Path(sys.argv[1]).parent / "security.json"
launch.owned_processes = lambda root: []
try:
    with terminal_lifetime() as closing:
        launch.run(types.SimpleNamespace(accept=True), closing)
except KeyboardInterrupt:
    pass
'''


class LifecycleTests(unittest.TestCase):
    def test_stop_matches_exact_checkout_and_mode(self):
        root = Path("/tmp/project with spaces")
        engine = root / "runtime/NapCat-QCE-macOS-arm64"
        self.assertEqual(lifecycle.role_for([str(engine / "qce-server")], root), "server")
        self.assertEqual(lifecycle.role_for([str(engine / "QQNapCatRuntime.app/Contents/MacOS/QQ")], root), "reader")
        self.assertIsNone(lifecycle.role_for(["/Applications/QQ.app/Contents/MacOS/QQ"], root))
        self.assertIsNone(lifecycle.role_for(["/other/project/qce-server"], root))
        self.assertIsNone(lifecycle.role_for([sys.executable, "-c", str(root / "launch.py")], root))
        self.assertIsNone(lifecycle.role_for([sys.executable, str(root / "archive.py"), "input.json"], root))
        self.assertIsNone(lifecycle.role_for([sys.executable, str(root / "launch.py"), "--stop"], root))
        self.assertEqual(lifecycle.role_for([sys.executable, str(root / "archive.py"), "--watch",
                                            "--security", str(root / "runtime/security/security.json")], root), "watcher")

    def test_pid_reuse_is_not_signalled(self):
        old = lifecycle.Identity(98765, "server", "/project/qce-server", "old start")
        reused = lifecycle.Identity(98765, "server", "/project/qce-server", "new start")
        with patch.object(lifecycle, "owned_processes", return_value=[reused]), patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(lifecycle.signal_matching(ROOT, [old], signal.SIGTERM), 0)
            kill.assert_not_called()

    def test_verified_process_can_be_stopped(self):
        item = lifecycle.Identity(98765, "server", "/project/qce-server", "start")
        with patch.object(lifecycle, "owned_processes", return_value=[item]), patch.object(lifecycle.os, "kill") as kill:
            self.assertEqual(lifecycle.signal_matching(ROOT, [item], signal.SIGTERM), 1)
            kill.assert_called_once_with(item.pid, signal.SIGTERM)

    def test_duplicate_launch_is_locked_until_first_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            script = "from pathlib import Path; from lifecycle import single_instance; import sys\nwith single_instance(Path(sys.argv[1])) as acquired: print(acquired)"
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            def second():
                return subprocess.check_output([sys.executable, "-c", script, folder], env=env, text=True).strip()
            with lifecycle.single_instance(Path(folder)) as first:
                self.assertTrue(first)
                self.assertEqual(second(), "False")
            self.assertEqual(second(), "True")

    def test_foreign_listener_is_detected_without_stopping_it(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with self.assertRaisesRegex(ValueError, "其他运行程序"):
                launch.check_ports((port,))
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                connection, _ = server.accept()
                connection.close()

    def test_recently_closed_listener_can_restart(self):
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                connection, _ = server.accept()
                connection.close()
                self.assertEqual(client.recv(1), b"")
        launch.check_ports((port,))

    def test_terminal_hangup_and_termination_stop_both_children(self):
        for sig in (signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as folder:
                ready = Path(folder) / "ready.json"
                process = subprocess.Popen([sys.executable, "-c", RUNNER, str(ready)],
                                           env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                ids = {}
                try:
                    wait_for(ready.exists)
                    ids = json.loads(ready.read_text())
                    process.send_signal(sig)
                    self.assertEqual(process.wait(timeout=6), 0, process.stderr.read().decode())
                    for pid in ids["children"]:
                        wait_for(lambda: not alive(pid))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stderr.close()
                    for pid in ids.get("children", []):
                        if alive(pid):
                            os.kill(pid, signal.SIGKILL)

    def test_parent_loss_also_cleans_children(self):
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / "ready.json"
            parent_code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],start_new_session=True); time.sleep(60)"
            parent = subprocess.Popen([sys.executable, "-c", parent_code, RUNNER, str(ready)],
                                      env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ids = {}
            try:
                wait_for(ready.exists)
                ids = json.loads(ready.read_text())
                parent.terminate()
                parent.wait(timeout=2)
                for pid in [ids["controller"], *ids["children"]]:
                    wait_for(lambda: not alive(pid))
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()
                for pid in [ids.get("controller", 0), *ids.get("children", [])]:
                    if pid and alive(pid):
                        os.kill(pid, signal.SIGKILL)

    def test_orphan_recovery_respects_unfinished_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / "security").mkdir()
            (runtime / "security/security.json").write_text(json.dumps({"accessToken":"test"}))
            server = lifecycle.Identity(98765, "server", "qce-server", "start")
            for status in ("pending", "running"):
                response = io.BytesIO(json.dumps({"success":True,"data":{"tasks":[{"status":status}]}}).encode())
                with self.subTest(status=status), patch.object(launch, "RUNTIME", runtime), patch.object(launch, "owned_processes", return_value=[server]), patch.object(launch, "build_opener") as opener, patch.object(launch, "stop_owned") as stop:
                    opener.return_value.open.return_value = response
                    with self.assertRaisesRegex(ValueError, "未完成任务"):
                        launch.recover_previous()
                    stop.assert_not_called()

    def test_idle_orphan_is_recovered(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / "security").mkdir()
            (runtime / "security/security.json").write_text(json.dumps({"accessToken":"test"}))
            server = lifecycle.Identity(98765, "server", "qce-server", "start")
            response = io.BytesIO(b'{"success":true,"data":{"tasks":[]}}')
            with patch.object(launch, "RUNTIME", runtime), patch.object(launch, "owned_processes", return_value=[server]), patch.object(launch, "build_opener") as opener, patch.object(launch, "stop_owned") as stop:
                opener.return_value.open.return_value = response
                self.assertTrue(launch.recover_previous())
                stop.assert_called_once_with(launch.ROOT, [server])


if __name__ == "__main__":
    unittest.main()
