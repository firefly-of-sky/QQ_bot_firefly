#!/usr/bin/env python3
"""Read-only native QQ experiment sampler. No OneBot/NapCat login assumptions.

Probe input (private run/native-qq-probe.json):
  checked_at: Unix seconds from a fresh GUI/client-log observation
  state: online | offline | unknown
  evidence: visual | client_log | manual_confirmation
  login_confirmed_at: Unix seconds, required on the initial online probe
Extra fields (window titles, account IDs, QR URLs, etc.) are never copied.
A running process alone is never evidence of an online QQ account.
"""
import argparse
from datetime import datetime, timezone
import gzip
import json
import math
import os
import re
from pathlib import Path
import subprocess
import time

try:
    from .collect_qq import bounded_run, prune
    from .ops import atomic_json, lock
except ImportError:
    from collect_qq import bounded_run, prune
    from ops import atomic_json, lock

CONTAINER = 'qqbot-native-qq-1'
DURATION = 24 * 3600
FRESH_SECONDS = 150
LOG_LIMIT = 128 * 1024
RETENTION_BYTES = 64 * 1024 * 1024
TERMINAL = {'QQ_OFFLINE', 'PROCESS_FAILED', 'PROCESS_RESTARTED',
            'ONLINE_WINDOW_COMPLETED', 'OBSERVATION_ENDED_UNVERIFIED', 'CANCELLED_BY_OPERATOR'}
EVIDENCE = {'visual', 'client_log', 'manual_confirmation'}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_json(path, limit=32768):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Private JSON exceeds its bounded input limit')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Expected private JSON object')
    return value


def probe_status(path, now):
    result = dict(state='unknown', fresh=False)
    try:
        source = read_json(path)
    except (OSError, ValueError, UnicodeError):
        return result
    checked = source.get('checked_at')
    if not number(checked) or not 0 <= now - checked <= FRESH_SECONDS:
        return result
    result.update(checked_at=checked, fresh=True)
    if source.get('evidence') not in EVIDENCE:
        return result
    result['evidence'] = source['evidence']
    if source.get('state') in ('online', 'offline'):
        result['state'] = source['state']
    confirmed = source.get('login_confirmed_at')
    if number(confirmed) and 0 < confirmed <= checked and now - confirmed <= FRESH_SECONDS:
        result['login_confirmed_at'] = confirmed
    if source.get('scope') == 'native_qq_gui_display_only':
        result['scope']='native_qq_gui_display_only'
        indicators=source.get('indicators',{})
        if isinstance(indicators,dict):
            result['indicators']={k:v for k,v in indicators.items() if
                (k in ('main_navigation','explicit_invalid_dialog') and type(v) is bool) or
                (k in ('green_pixels','status_regions_sampled','status_capture_failures') and type(v) is int and 0<=v<=4096)}
    return result


def container_status(run):
    try:
        result = run(['docker', 'inspect', '--format', '{{json .State}}', CONTAINER],
                     timeout=10, limit=32768)
        if result.returncode or result.timed_out:
            return {'state': 'unknown'}
        raw = json.loads(result.stdout)
        if not isinstance(raw, dict):
            return {'state': 'unknown'}
        status = raw.get('Status')
        if status not in ('running', 'exited', 'dead', 'created', 'restarting', 'paused', 'removing'):
            status = 'unknown'
        value = dict(state=status, oom_killed=raw.get('OOMKilled') is True)
        # Docker emits an ISO timestamp, never use arbitrary error text as a summary.
        started = raw.get('StartedAt')
        if isinstance(started, str):
            try:
                # Ubuntu 22.04's Python 3.10 rejects Docker's 9-digit fraction.
                validated=re.sub(r'(\.\d{6})\d+', r'\1', started)
                datetime.fromisoformat(validated.replace('Z', '+00:00'))
                value['started_at'] = started
            except ValueError:
                pass
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'unknown'}


def advance(saved, process, probe, now):
    saved.setdefault('outcome', 'WAITING_LOGIN')
    saved.setdefault('duration_seconds', DURATION)
    saved.setdefault('first_sample_at', now)
    latest = saved.get('latest', {})
    previous = latest.get('checked_at')
    gap = now - previous if number(previous) else 0
    saved['max_sample_gap_seconds'] = max(saved.get('max_sample_gap_seconds', 0), gap)
    saved['latest'] = dict(checked_at=now, container=process, qq=probe)
    if saved['outcome'] in TERMINAL:
        return saved
    if saved['outcome'] == 'WAITING_LOGIN':
        if (process['state'] == 'running' and probe['state'] == 'online'
                and 'login_confirmed_at' in probe):
            # Start at this actual sample; never backdate an unobserved interval.
            saved.update(outcome='OBSERVING', started_at=now,
                         login_confirmed_at=probe['login_confirmed_at'],
                         initial_container_started_at=process.get('started_at'),
                         online_samples=0, unknown_samples=0, coverage_verified=True,
                         max_observation_gap_seconds=0)
            previous, gap = None, 0
        else:
            return saved
    if process['state'] in ('exited', 'dead', 'created', 'restarting', 'paused', 'removing'):
        saved.update(outcome='PROCESS_FAILED', finished_at=now)
        return saved
    original = saved.get('initial_container_started_at')
    if original and process.get('started_at') and process['started_at'] != original:
        saved.update(outcome='PROCESS_RESTARTED', finished_at=now)
        return saved
    if probe['state'] == 'offline':
        saved.update(outcome='QQ_OFFLINE', finished_at=now)
        return saved
    online = process['state'] == 'running' and probe['state'] == 'online'
    if online:
        saved['online_samples'] += 1
    else:
        saved['unknown_samples'] += 1
        saved['coverage_verified'] = False
    saved['max_observation_gap_seconds'] = max(saved.get('max_observation_gap_seconds', 0), gap)
    if previous is not None and (gap < 0 or gap > FRESH_SECONDS):
        saved['coverage_verified'] = False
    saved['elapsed_seconds'] = max(0, now - saved['started_at'])
    if saved['elapsed_seconds'] >= saved['duration_seconds']:
        outcome = ('ONLINE_WINDOW_COMPLETED' if online and saved['coverage_verified']
                   else 'OBSERVATION_ENDED_UNVERIFIED')
        saved.update(outcome=outcome, finished_at=now)
    return saved


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def collect(root='/opt/qqbot', *, now=None, run=bounded_run):
    root = Path(root)
    state = root / 'run'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time() if now is None else now
    path = state / 'native-qq-observation.json'
    saved = read_json(path) if path.exists() else {}
    process = container_status(run)
    probe = probe_status(state / 'native-qq-probe.json', now)
    advance(saved, process, probe, now)
    start = max(now - 72 * 3600, min(saved.get('logs_until', now - 300), now))
    folder = state / 'native-qq-logs'
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    private_log = dict(available=False)
    try:
        result = run(['docker', 'logs', '--timestamps', '--since', iso(start),
                      '--until', iso(now), '--tail', '1000', CONTAINER],
                     timeout=20, limit=LOG_LIMIT)
        available = result.returncode == 0 and not result.timed_out
        private_log = dict(available=available, text=result.stdout,
                           truncated=result.truncated, bytes_seen=result.bytes_seen,
                           line_limit=1000,
                           line_limit_may_have_truncated=result.stdout.count('\n') >= 1000)
        if available:
            saved['logs_until'] = now
    except (OSError, subprocess.SubprocessError):
        pass
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = folder / ('sample-' + stamp + '.json.gz')
    temporary = target.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb') as archive:
            archive.write(json.dumps(dict(captured_at=iso(now), process=process,
                probe=probe, logs_from=iso(start), logs_until=iso(now), log=private_log),
                ensure_ascii=False).encode())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    saved['log_collection_ok'] = private_log['available']
    saved['last_sample'] = target.name
    saved['retained'] = prune(folder, now, max_bytes=RETENTION_BYTES)
    atomic_json(path, saved)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/opt/qqbot')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    path = Path(args.root) / 'run/native-qq-observation.json'
    try:
        if args.summary:
            result = read_json(path) if path.exists() else {'outcome': 'NOT_STARTED'}
        else:
            os.umask(0o077)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with lock(path.parent / 'native-qq-observation.lock'):
                result = collect(args.root)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(operation='observe_native_qq', error=type(error).__name__,
                             detail='Check native QQ sampler privately; raw data omitted')))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
