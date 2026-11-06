#!/usr/bin/env python3
"""Read-only probe of native QQ's displayed GUI status, not a server heartbeat.

Calibrated for the installed Linux QQ GUI: main navigation plus a visible small
status button with green pixels means only "GUI displays online". Absence of
green never means offline. Login/QR views count as offline only after explicit
login confirmation. Chat text resembling an offline notice is not sufficient.
Screenshots/RGB buffers, accessibility names and account identifiers are neither
written to disk nor printed. ffmpeg captures only a status-button rectangle.
"""
import argparse
import json
import math
import os
import re
import signal
import subprocess
import time

class ProbeTimeout(BaseException):
    pass

try:
    from .native_qq_accessibility import collect_features, configure_bus, INVALID_PHRASES
except ImportError:
    from native_qq_accessibility import collect_features, configure_bus, INVALID_PHRASES


def green_pixel_count(rgb):
    if not isinstance(rgb, bytes) or len(rgb) % 3:
        raise ValueError('Expected bounded RGB24 bytes')
    if len(rgb) > 64 * 64 * 3:
        raise ValueError('Status rectangle exceeds capture budget')
    return sum(g > 110 and g-r > 35 and g-b > 10
               for r,g,b in zip(rgb[0::3],rgb[1::3],rgb[2::3]))


def status_rectangles(snapshot):
    """Use calibrated 6..12px own-header status controls, not profile/contact UI."""
    values=[]
    for app in snapshot.get('extended_apps', []):
        for control in app.get('controls', []):
            flags=control.get('name_flags', {});descriptions=control.get('description_flags', {})
            if not (flags.get('status_control') or descriptions.get('status_control')):
                continue
            detail=control.get('status_details', {})
            if detail.get('showing') is not True or detail.get('visible') is not True:
                continue
            rect=detail.get('screen_bbox', {})
            try:
                coords=tuple(rect[k] for k in ('x','y','width','height'))
                if not all(type(x) is int for x in coords):continue
                x,y,w,h=coords
                if not (0<=x<=100000 and 0<=y<=100000 and 6<=w<=12 and 6<=h<=12):continue
            except (KeyError,TypeError):continue
            if coords not in values:values.append(coords)
    return values[:2]


def capture_green(rect, *, display=None, run=subprocess.run):
    display=display or os.environ.get('DISPLAY', ':1')
    if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display):
        raise ValueError('Only a local X11 display is allowed')
    x,y,w,h=rect
    result=run(['ffmpeg','-nostdin','-loglevel','error','-f','x11grab',
                '-video_size',str(w)+'x'+str(h),'-i',display+'+'+str(x)+','+str(y),
                '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-threads','1','-'],
               capture_output=True,timeout=3,check=False)
    if result.returncode or len(result.stdout)!=w*h*3:
        raise RuntimeError('Status region capture unavailable')
    return green_pixel_count(result.stdout)


def classify(snapshot, green_counts=(), *, login_confirmed=False,
             explicit_invalid_dialog=False):
    """No arbitrary text or window titles become login evidence here."""
    features=snapshot.get('features', {})
    main=all(features.get(k) is True for k in
             ('search_input','message_navigation','contacts_navigation'))
    login_view=not main and (features.get('login_button') is True or features.get('qr_image') is True)
    unsafe=any(features.get(k) is True for k in
               ('login_button','relogin_button','qr_image','qr_login_label','identity_invalid_notice'))
    # Fixed word counts are candidates; notices only block an online conclusion.
    connection_warning=False
    for app in snapshot.get('extended_apps', []):
        words=app.get('fixed_word_counts', {})
        if any(words.get(k,0)>0 for k in ('reconnecting','network_error','connection_lost','identity_invalid')):
            connection_warning=True
    if login_confirmed and (login_view or explicit_invalid_dialog):
        return 'offline'
    if (snapshot.get('qq_application_found') is True and not snapshot.get('traversal_truncated')
            and snapshot.get('nodes_failed',0)==0 and main and not unsafe and not connection_warning
            and any(type(count) is int and count>=3 for count in green_counts)):
        return 'online'
    return 'unknown'


def visible_invalid_dialog(registry, state_ids, *, timeout=1.5):
    """Require an actual visible modal dialog, not a matching chat message.

    If QQ exposes a notice as a plain frame/panel we stay conservative (unknown).
    This reader returns only a bool and never opens/closes a window.
    """
    deadline=time.monotonic()+timeout
    desktop=registry.getDesktop(0)
    try:
        from .native_qq_accessibility import qq_application_name
    except ImportError:
        from native_qq_accessibility import qq_application_name
    queue=[];count=0
    for i in range(min(int(desktop.childCount),100)):
        app=desktop.getChildAtIndex(i)
        if qq_application_name(app.name):queue.append((app,False))
    while queue and count<500 and time.monotonic()<deadline:
        node,in_visible_dialog=queue.pop(0);count+=1
        try:
            role=node.getRoleName().lower()
            if role=='dialog':
                states=node.getState()
                in_visible_dialog=(states.contains(state_ids['showing']) and states.contains(state_ids['visible']))
            text=node.name.lower()[:500]
            if in_visible_dialog and role in ('label','text') and any(p in text for p in INVALID_PHRASES):
                return True
            for i in range(min(int(node.childCount),max(0,500-count-len(queue)),100)):
                queue.append((node.getChildAtIndex(i),in_visible_dialog))
        except Exception:continue
    return False


def probe(*, confirmed_at=None, snapshot_reader=collect_features, capture=capture_green,
          invalid_dialog=False, now=None):
    now=time.time() if now is None else now
    snapshot=snapshot_reader(extended=True,max_nodes=2500,timeout=4)
    counts=[];captures_failed=0
    for rect in status_rectangles(snapshot):
        try:counts.append(capture(rect))
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):captures_failed+=1
    confirmed=(type(confirmed_at) in (int,float) and math.isfinite(confirmed_at) and 0<confirmed_at<=now)
    state=classify(snapshot,counts,login_confirmed=confirmed,
                   explicit_invalid_dialog=invalid_dialog)
    features=snapshot.get('features', {})
    result=dict(checked_at=now,state=state,evidence='visual',
                scope='native_qq_gui_display_only',
                indicators=dict(main_navigation=all(features.get(k) is True for k in
                    ('search_input','message_navigation','contacts_navigation')),
                    green_pixels=max(counts) if counts else None,
                    status_regions_sampled=len(counts),status_capture_failures=captures_failed,
                    explicit_invalid_dialog=bool(invalid_dialog)))
    if confirmed:result['login_confirmed_at']=confirmed_at
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--login-confirmed-at',type=float,
                        default=os.environ.get('QQ_NATIVE_LOGIN_CONFIRMED_AT'),
                        help='Unix timestamp of the user-confirmed native login')
    args=parser.parse_args()
    def expired(signum,frame):raise ProbeTimeout('Probe time budget reached')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,12)
    try:
        configure_bus()
        import pyatspi
        invalid=visible_invalid_dialog(pyatspi.Registry,
            dict(showing=pyatspi.STATE_SHOWING,visible=pyatspi.STATE_VISIBLE))
        result=probe(confirmed_at=args.login_confirmed_at,invalid_dialog=invalid)
    except (ProbeTimeout, Exception) as error:
        result=dict(checked_at=time.time(),state='unknown',evidence='visual',
                    scope='native_qq_gui_display_only',error=type(error).__name__)
    finally:signal.setitimer(signal.ITIMER_REAL,0)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
