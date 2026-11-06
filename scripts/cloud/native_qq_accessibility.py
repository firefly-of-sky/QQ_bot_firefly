#!/usr/bin/env python3
"""Read-only bounded AT-SPI candidate features for native QQ GUI calibration.

Run as the native QQ desktop user. This helper never infers that a QQ window or
an "online" word proves account login. Its state remains unknown until a reviewed
GUI-specific classifier is supplied; --features is for calibration only.
It never prints accessibility names, chat text, window titles or a D-Bus address.
"""
import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import signal
import time

class ProbeTimeout(BaseException):
    pass

QQ_APPS = frozenset(('qq', 'linuxqq', '腾讯qq'))
ROLES = {
    'application': 'application', 'frame': 'frame', 'dialog': 'dialog',
    'window': 'window', 'panel': 'panel', 'label': 'label', 'text': 'text',
    'entry': 'entry', 'password text': 'password', 'push button': 'button',
    'button': 'button', 'toggle button': 'toggle', 'check box': 'checkbox',
    'combo box': 'combobox', 'image': 'image', 'list': 'list',
    'list item': 'list_item', 'page tab': 'tab', 'menu item': 'menu_item',
}
CONTROLS = frozenset(('button', 'toggle', 'checkbox', 'combobox', 'tab', 'menu_item'))
TEXT_ROLES = frozenset(('label', 'text', 'dialog', 'frame', 'window', 'panel'))
FEATURES = (
    'login_button', 'relogin_button', 'qr_login_label', 'qr_image',
    'identity_invalid_notice', 'search_input', 'message_navigation',
    'contacts_navigation', 'online_status_control', 'offline_status_control',
    'password_input',
)
INVALID_PHRASES = (
    '你的账号当前登录已失效', '你的用户身份已失效', '当前登录已失效',
    '登录已失效', '你已被强制下线', '已被迫下线',
    '帐号在另一台电脑登录', '账号在另一台电脑登录',
)
QR_PHRASES = (
    '扫码登录', '扫描二维码登录', '二维码已失效', '二维码已过期',
    '请使用手机qq扫码', '使用手机qq扫码', 'scan the qr code',
)
EXTENDED_FLAGS = ('avatar', 'status_control', 'online', 'offline', 'reconnecting',
                  'network_error', 'connection_lost', 'identity_invalid', 'login_action')


def extended_flags(value):
    """Match only fixed, reviewed words; never return a substring of provider text."""
    text = value.strip().lower()[:500] if isinstance(value, str) else ''
    return dict(
        avatar=any(x in text for x in ('头像', 'avatar')),
        status_control=any(x in text for x in ('在线状态', '切换状态', '当前状态', '设置状态', 'online status')),
        online=text in ('在线', '当前在线', '已连接', '连接正常', 'online', 'connected',
                        '在线状态 在线', '在线状态：在线', '在线状态:在线'),
        offline=text in ('离线', '已离线', '当前离线', 'offline', '在线状态 离线', '在线状态：离线'),
        reconnecting=any(x in text for x in ('重新连接', '正在连接', '重连中', '连接中', 'reconnecting')),
        network_error=any(x in text for x in ('网络异常', '网络错误', '网络不可用', '检查网络', 'network error', 'network unavailable')),
        connection_lost=any(x in text for x in ('连接断开', '连接已断开', '已断开连接', '连接失效', 'connection lost', 'disconnected')),
        identity_invalid=any(x in text for x in INVALID_PHRASES),
        login_action=text in ('登录', '重新登录', '立即登录', 'log in', 'login', 'relogin'),
    )


def summarize_extended(records):
    """Records: role/name/description/checked/selected/state_available/path.

    Structural paths identify controls for a human calibration; names and
    descriptions are represented solely by fixed boolean matches.
    """
    counts = {key: 0 for key in EXTENDED_FLAGS}
    controls = []
    for record in records:
        role, name, description, checked, selected, available, path = record[:7]
        canonical = ROLES.get(role.strip().lower() if isinstance(role, str) else '', 'other')
        names = extended_flags(name)
        descriptions = extended_flags(description)
        for key in counts:
            counts[key] += int(names[key] or descriptions[key])
        if canonical in CONTROLS or (canonical == 'image' and any(names.values())):
            if len(controls) < 40:
                control = dict(path=list(path[:24]), role=canonical,
                    named=bool(name.strip()) if isinstance(name, str) else False,
                    described=bool(description.strip()) if isinstance(description, str) else False,
                    name_flags=names, description_flags=descriptions,
                    state_available=available, checked=checked, selected=selected)
                if len(record) > 7 and record[7]:
                    control['status_details'] = record[7]
                controls.append(control)
    return dict(fixed_word_counts=counts, controls=controls,
                controls_limit=40, classification='uncalibrated_candidates_only')


def numeric_control_details(node, state_ids=None, coordinate_type=0):
    """Only for known avatar/status controls: flags, screen rectangle and numbers."""
    details = {}
    try:
        states = node.getState()
        for name in ('showing', 'visible'):
            if state_ids and name in state_ids:
                details[name] = bool(states.contains(state_ids[name]))
    except Exception:
        pass
    try:
        rect = node.queryComponent().getExtents(coordinate_type)
        try:
            raw = (rect.x, rect.y, rect.width, rect.height)
        except AttributeError:
            raw = tuple(rect)
        if (len(raw) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 100000 for v in raw)
                and raw[2] >= 0 and raw[3] >= 0):
            details['screen_bbox'] = dict(zip(('x','y','width','height'), (int(v) for v in raw)))
    except Exception:
        pass
    try:
        interface = node.queryValue()
        values = dict(current=interface.currentValue, minimum=interface.minimumValue,
                      maximum=interface.maximumValue)
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 1e12 for v in values.values()):
            details['numeric_value'] = {key:round(value, 4) for key,value in values.items()}
    except Exception:
        pass
    return details


def qq_application_name(name):
    return isinstance(name, str) and name.strip().lower() in QQ_APPS


def summarize_records(records):
    """Reduce (role, name) pairs to fixed counters/booleans, discarding all names."""
    features = dict.fromkeys(FEATURES, False)
    counts = dict.fromkeys(tuple(dict.fromkeys(ROLES.values())) + ('other',), 0)
    nodes = 0
    for role, name in records:
        canonical = ROLES.get(role.strip().lower() if isinstance(role, str) else '', 'other')
        counts[canonical] += 1
        nodes += 1
        # No entire long message must be scanned to derive these candidate flags.
        text = name.strip().lower()[:500] if isinstance(name, str) else ''
        if canonical in CONTROLS:
            if text in ('登录', '立即登录', 'log in', 'login'):
                features['login_button'] = True
            if text in ('重新登录', '重新登陆', 'relogin', 'log in again'):
                features['relogin_button'] = True
            if text in ('消息', '聊天', 'messages', 'chats'):
                features['message_navigation'] = True
            if text in ('联系人', 'contacts'):
                features['contacts_navigation'] = True
            if text in ('在线', '在线状态 在线', '在线状态：在线', '在线状态:在线',
                        'online', 'online status online'):
                features['online_status_control'] = True
            if text in ('离线', '离线状态', '在线状态 离线', '在线状态：离线',
                        'offline', 'online status offline'):
                features['offline_status_control'] = True
        if canonical in ('entry', 'text') and text in ('搜索', 'search'):
            features['search_input'] = True
        if canonical == 'password':
            features['password_input'] = True
        if canonical == 'image' and text in ('二维码', '登录二维码', 'qr code', 'qrcode'):
            features['qr_image'] = True
        if canonical in TEXT_ROLES:
            if any(fragment in text for fragment in QR_PHRASES):
                features['qr_login_label'] = True
            if any(fragment in text for fragment in INVALID_PHRASES):
                features['identity_invalid_notice'] = True
    return dict(nodes_seen=nodes, roles=counts, features=features)


def walk_application(app, *, max_nodes, deadline, clock=time.monotonic,
                     extended=False, state_ids=None, coordinate_type=0):
    """Bound traversal breadth/depth; names remain inside this process only."""
    queue = deque([(app, ())])
    records = []
    extended_records = []
    failed = 0
    truncated = False
    while queue:
        if len(records) + failed >= max_nodes or clock() >= deadline:
            truncated = True
            break
        node, path = queue.popleft()
        try:
            role = node.getRoleName()
            name = node.name
            records.append((role, name))
            if extended:
                try:
                    description = node.description
                except Exception:
                    description = ''
                checked = selected = available = False
                if ROLES.get(role.strip().lower(), 'other') in CONTROLS and state_ids:
                    try:
                        states = node.getState()
                        checked = bool(states.contains(state_ids['checked']))
                        selected = bool(states.contains(state_ids['selected']))
                        available = True
                    except Exception:
                        pass
                flags = extended_flags(name)
                desc_flags = extended_flags(description)
                is_control = ROLES.get(role.strip().lower(), 'other') in CONTROLS | {'image'}
                details = numeric_control_details(node,state_ids,coordinate_type) if is_control and (
                    flags['avatar'] or flags['status_control'] or desc_flags['avatar'] or desc_flags['status_control']) else {}
                extended_records.append((role, name, description, checked, selected, available, path, details))
        except Exception:
            failed += 1
            continue
        try:
            # Some providers expose huge virtual lists; never enqueue without cap.
            count = max(0, int(node.childCount))
            budget = max_nodes - len(records) - failed - len(queue)
            count_to_read = min(count, max(0, budget), 100)
            if count_to_read < count:
                truncated = True
            for index in range(count_to_read):
                if clock() >= deadline:
                    truncated = True
                    break
                try:
                    child = node.getChildAtIndex(index)
                    if child is not None:
                        queue.append((child, path + (index,)))
                except Exception:
                    failed += 1
        except Exception:
            failed += 1
    value = summarize_records(records)
    value.update(nodes_failed=failed, traversal_truncated=truncated)
    if extended:
        value['extended'] = summarize_extended(extended_records)
    return value


def configure_bus(path='/tmp/native-dbus-address'):
    """Resolve the private per-desktop bus locally; never include it in output."""
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        with Path(path).open('r') as stream:
            value = stream.read(2049).strip()
        if len(value) > 2048 or not value.startswith(('unix:path=', 'unix:abstract=')):
            raise ValueError('Unsupported private desktop bus address')
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = value
    os.environ.setdefault('DISPLAY', ':99')


def collect_features(*, max_nodes=2000, timeout=5, registry=None, clock=time.monotonic,
                     extended=False, state_ids=None, coordinate_type=0):
    started = clock()
    deadline = started + timeout
    if registry is None:
        configure_bus()
        import pyatspi
        registry = pyatspi.Registry
        state_ids = dict(checked=pyatspi.STATE_CHECKED, selected=pyatspi.STATE_SELECTED,
                         showing=pyatspi.STATE_SHOWING, visible=pyatspi.STATE_VISIBLE)
        coordinate_type = pyatspi.DESKTOP_COORDS
    desktop = registry.getDesktop(0)
    apps = []
    # Enumerating desktop application names is necessary for exact QQ filtering;
    # neither these names nor unselected application trees are returned or logged.
    desktop_count = min(max(0, int(desktop.childCount)), 100)
    for index in range(desktop_count):
        if clock() >= deadline:
            break
        try:
            app = desktop.getChildAtIndex(index)
            if qq_application_name(app.name):
                apps.append(app)
        except Exception:
            continue
    failed = 0
    truncated = clock() >= deadline
    # Aggregate each QQ process separately while maintaining one global budget.
    aggregate = summarize_records(())
    extended_apps = []
    for app in apps[:8]:
        remaining = max_nodes - aggregate['nodes_seen'] - failed
        if remaining <= 0 or clock() >= deadline:
            truncated = True
            break
        value = walk_application(app, max_nodes=remaining, deadline=deadline, clock=clock,
                                 extended=extended, state_ids=state_ids, coordinate_type=coordinate_type)
        if extended:
            extended_apps.append(value['extended'])
        aggregate['nodes_seen'] += value['nodes_seen']
        for key in aggregate['roles']:
            aggregate['roles'][key] += value['roles'][key]
        for key in aggregate['features']:
            aggregate['features'][key] = aggregate['features'][key] or value['features'][key]
        failed += value['nodes_failed']
        truncated = truncated or value['traversal_truncated']
    result = dict(checked_at=time.time(), state='unknown', source='atspi_candidate_features',
                qq_application_found=bool(apps), qq_application_count=min(len(apps), 8),
                nodes_seen=aggregate['nodes_seen'], nodes_failed=failed,
                roles=aggregate['roles'], features=aggregate['features'],
                traversal_truncated=truncated, elapsed_ms=round((clock()-started)*1000))
    if extended:
        result['extended_apps'] = extended_apps
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features', action='store_true', help='Output candidate flags only; no online classification')
    parser.add_argument('--features2', action='store_true', help='Also inspect fixed description words and checked/selected control flags; no classification')
    parser.add_argument('--max-nodes', type=int, default=2000)
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args()
    if not 10 <= args.max_nodes <= 10000 or not 0.5 <= args.timeout <= 15:
        parser.error('Use 10..10000 nodes and 0.5..15 seconds')
    def expired(signum, frame):
        raise ProbeTimeout('Accessibility time limit reached')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        result = collect_features(max_nodes=args.max_nodes, timeout=args.timeout,
                                  extended=args.features2)
    except (ProbeTimeout, Exception) as error:
        # Exception text from providers can contain arbitrary window/chat data.
        result = dict(checked_at=time.time(), state='unknown',
                      source='atspi_candidate_features', error=type(error).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
