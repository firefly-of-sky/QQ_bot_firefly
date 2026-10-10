"""Manage only this checkout's reader processes and terminal lifetime."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import fcntl
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import time


@dataclass(frozen=True)
class Identity:
    pid: int
    role: str
    command: str
    started: str


def role_for(words: list[str], root: Path) -> str | None:
    engine = root / "runtime" / "NapCat-QCE-macOS-arm64"
    if not words:
        return None
    if words[0] == str(engine / "qce-server"):
        return "server"
    if words[0] == str(engine / "QQNapCatRuntime.app" / "Contents" / "MacOS" / "QQ"):
        return "reader"
    if len(words) > 1:
        if words[0] in ("bash", "/bin/bash") and words[1] == str(engine / "launcher-user.sh"):
            return "reader"
        python = Path(words[0]).name.startswith("python") or Path(words[0]).name == "Python"
        if python and words[1] == str(root / "launch.py"):
            return None if "--stop" in words or "--check" in words else "controller"
        if python and words[1] == str(root / "archive.py") and "--watch" in words:
            try:
                if words[words.index("--security") + 1] == str(root / "runtime/security/security.json"):
                    return "watcher"
            except (ValueError, IndexError):
                pass
    return None


def owned_processes(root: Path) -> list[Identity]:
    rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,uid=,command="], text=True)
    found = []
    for row in rows.splitlines():
        fields = row.strip().split(None, 2)
        if len(fields) != 3 or int(fields[1]) != os.getuid() or int(fields[0]) == os.getpid():
            continue
        try:
            role = role_for(shlex.split(fields[2]), root)
        except ValueError:
            continue
        if role:
            pid = int(fields[0])
            started = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
                                     capture_output=True, text=True, check=False).stdout.strip()
            if started:
                found.append(Identity(pid, role, fields[2], started))
    return found


def signal_matching(root: Path, identities: list[Identity], sig: int) -> int:
    # Revalidate executable arguments, owner and start time before signalling:
    # an unrelated process that reused an old PID must never be stopped.
    current = set(owned_processes(root))
    count = 0
    for item in identities:
        if item in current:
            try:
                os.kill(item.pid, sig)
                count += 1
            except ProcessLookupError:
                pass
    return count


def stop_owned(root: Path, identities: list[Identity] | None = None) -> int:
    identities = owned_processes(root) if identities is None else identities
    count = signal_matching(root, identities, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not set(identities).intersection(owned_processes(root)):
            return count
        time.sleep(.2)
    signal_matching(root, identities, signal.SIGKILL)
    return count


@contextmanager
def terminal_lifetime():
    """Turn terminal close/reparenting into an exception so finally runs."""
    parent = os.getppid()
    finished = threading.Event()

    def interrupted(*_):
        if not finished.is_set():
            raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupted)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def watch_parent():
        while not finished.wait(.25):
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=watch_parent, daemon=True)
    thread.start()
    try:
        yield finished
    finally:
        finished.set()
        thread.join(timeout=1)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextmanager
def single_instance(runtime: Path):
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "launch.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
