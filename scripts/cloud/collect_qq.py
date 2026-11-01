#!/usr/bin/env python3
"""Private, bounded server-side log sampling; never restart or send QQ messages."""
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from types import SimpleNamespace

try:
    from .ops import Operations, atomic_json, lock
except ImportError:
    from ops import Operations, atomic_json, lock

LOG_BYTES = 512 * 1024
RETENTION_BYTES = 256 * 1024 * 1024
RETENTION_SECONDS = 72 * 3600
OBSERVATION_SECONDS = 6 * 3600


def bounded_run(args, timeout=20, limit=LOG_BYTES):
    """Drain output while retaining only a bounded tail, including on noisy errors."""
    output = bytearray()
    total = 0
    expired = False
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            for key, _ in selector.select(min(remaining, 0.5)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                output.extend(chunk)
                if len(output) > limit:
                    del output[:-limit]
        if expired:
            os.killpg(process.pid, signal.SIGKILL)
        code = process.wait(timeout=2)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return SimpleNamespace(stdout=output.decode('utf-8', errors='replace'),
                           returncode=code, truncated=total > limit,
                           bytes_seen=total, timed_out=expired)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def prune(folder, now, max_bytes=RETENTION_BYTES, max_age=RETENTION_SECONDS):
    files = sorted(folder.glob('sample-*.json.gz'))
    for path in files[:]:
        if now - path.stat().st_mtime > max_age:
            path.unlink()
            files.remove(path)
    sizes = {p: p.stat().st_size for p in files}
    total = sum(sizes.values())
    for path in files:
        if total <= max_bytes:
            break
        total -= sizes[path]
        path.unlink()
    return dict(files=sum(1 for _ in folder.glob('sample-*.json.gz')), bytes=total)


def qq_state(status):
    app = status.get('application', {})
    if app.get('qq_connected') is True and app.get('qq_online') is True:
        return 'online'
    login = status.get('qq_login', {})
    if login.get('available') is True:
        if login.get('online') is True:
            return 'online'
        if login.get('offline') is True or login.get('phase') == 'waiting_qrcode':
            return 'offline'
    return 'unknown'


def advance_observation(saved, status, now, seed=None):
    online = qq_state(status)
    observation = saved.setdefault('observation', {'outcome': 'WAITING_LOGIN',
                                                   'duration_seconds': OBSERVATION_SECONDS})
    if observation['outcome'] == 'WAITING_LOGIN' and online == 'online' and status.get('application', {}).get('ready') is True:
        started = seed if isinstance(seed, (int, float)) and 0 < seed <= now else now
        observation.update(outcome='OBSERVING', started_at=started, probe_failures=0)
    if observation['outcome'] == 'OBSERVING':
        if online == 'offline':
            observation.update(outcome='QQ_OFFLINE', finished_at=now)
        elif online == 'unknown':
            observation['probe_failures'] = observation.get('probe_failures', 0) + 1
            if observation['probe_failures'] >= 3:
                observation.update(outcome='PROBE_UNAVAILABLE', finished_at=now)
        else:
            observation['probe_failures'] = 0
            if now - observation['started_at'] >= observation['duration_seconds']:
                observation.update(outcome='ONLINE_WINDOW_COMPLETED', finished_at=now)
    saved['latest'] = dict(checked_at=now, qq=online,
                          bot_live=status.get('application', {}).get('live'),
                          bot_ready=status.get('application', {}).get('ready'),
                          reason=status.get('application', {}).get('reason'))


def collect(operation, now=None, log_run=bounded_run):
    now = time.time() if now is None else now
    state_file = operation.state / 'qq-log-collector.json'
    saved = json.loads(state_file.read_text()) if state_file.exists() else {}
    release = (operation.root / 'current').resolve().name
    if saved.get('release') not in (None, release):
        saved['previous_observation'] = saved.pop('observation', {})
    saved['release'] = release
    folder = operation.state / 'qq-logs'
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    status = operation.status()
    # Probe the QQ login API independently when the business process is unavailable.
    if qq_state(status) == 'unknown':
        status['qq_login'] = operation.qq_login_status()
    start = min(saved.get('logs_until', now - 300), now)
    start = max(start, now - RETENTION_SECONDS)
    logs = {}
    all_available = True
    log_operation = Operations(operation.root, run=log_run)
    for service in ('napcat', 'bot'):
        try:
            result = log_operation.compose('logs', '--no-color', '--timestamps',
                '--since', iso(start), '--until', iso(now), '--tail', '1000', service)
            available = result.returncode == 0 and not result.timed_out
            logs[service] = dict(available=available, text=result.stdout,
                                 truncated=result.truncated, bytes_seen=result.bytes_seen,
                                 line_limit=1000, line_limit_may_have_truncated=result.stdout.count('\n') >= 1000)
        except (OSError, subprocess.SubprocessError):
            available = False
            logs[service] = dict(available=False)
        all_available = all_available and available
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = folder / ('sample-' + stamp + '.json.gz')
    record = dict(captured_at=iso(now), release=release, logs_from=iso(start), logs_until=iso(now),
                  status=status, logs=logs)
    # Raw Docker logs may contain private messages or credentials. Never print them.
    temporary = target.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb') as archive:
            archive.write(json.dumps(record, ensure_ascii=False).encode())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if all_available:
        saved['logs_until'] = now
    saved['log_collection_ok'] = all_available
    seed = None
    seed_path = operation.state / 'qq-access41842-followup.json'
    if not saved.get('observation') and release == '5.0.1-napcat41842-r2' and seed_path.exists():
        seed = json.loads(seed_path.read_text()).get('first_ready_at')
    previous_check = saved.get('latest', {}).get('checked_at')
    saved['last_sample_gap_seconds'] = max(0, now - previous_check) if previous_check else 0
    saved['max_sample_gap_seconds'] = max(saved.get('max_sample_gap_seconds', 0), saved['last_sample_gap_seconds'])
    advance_observation(saved, status, now, seed)
    # Catch an actual kick even if a login/reconnect completed between samples.
    observation = saved['observation']
    for log in logs.values():
        for line in log.get('text', '').splitlines():
            if '[QQbotDiag/v1] ' not in line:
                continue
            try:
                row = json.loads(line.split('[QQbotDiag/v1] ', 1)[1])
                at = datetime.fromisoformat(row['at'].replace('Z', '+00:00')).timestamp()
                if row.get('event') != 'qq_kicked_offline' or at < observation.get('started_at', now + 1):
                    continue
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if observation['outcome'] in ('OBSERVING', 'ONLINE_WINDOW_COMPLETED'):
                observation.update(outcome='NATIVE_KICK_OBSERVED', finished_at=at)
    saved['retained'] = prune(folder, now)
    saved['last_sample'] = target.name
    saved['collector_started_at'] = saved.get('collector_started_at', now)
    atomic_json(state_file, saved)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/opt/qqbot')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    operation = Operations(args.root)
    try:
        if args.summary:
            path = operation.state / 'qq-log-collector.json'
            result = json.loads(path.read_text()) if path.exists() else {'state': 'not_started'}
        else:
            with lock(operation.state / 'qq-log-collector.lock'):
                result = collect(operation)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': type(error).__name__, 'operation': 'collect_qq',
                          'detail': 'Check collector service; private logs omitted'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
