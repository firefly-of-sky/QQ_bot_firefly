"""Bounded, explicitly requested QQ-without-Bot observation; always resume Bot."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

if __package__:
    from .ops import Operations, atomic_json, lock
else:
    from ops import Operations, atomic_json, lock


def resume(ops,report,*,sleep=time.sleep):
    marker=ops.state/'maintenance'
    if not marker.exists() or marker.read_text()!=report.get('lease'):return False
    for attempt in range(30):
        try:
            with lock(ops.state/'operations.lock'):
                if marker.read_text()!=report['lease']:return False
                ops.compose('up','-d','--no-deps','--no-build','--pull','never','bot')
                marker.unlink();return True
        except RuntimeError:
            if attempt==29:raise
            sleep(1)
    return False


def observe(ops,duration,*,now=time.monotonic,sleep=time.sleep):
    marker=ops.state/'maintenance';lease=uuid.uuid4().hex
    report=ops.state/'qq-isolation.json'
    result=dict(outcome='STARTING',duration_seconds=duration,bot_resumed=False,lease=lease)
    with lock(ops.state/'operations.lock'):
        if marker.exists():raise RuntimeError('Existing maintenance must finish first')
        if ops.napcat_api()('/QQLogin/CheckLoginStatus').get('isLogin') is not True:
            raise RuntimeError('A confirmed QQ login is required before the comparison')
        fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(lease)
        atomic_json(report,result)
    started=now();failures=0
    try:
        ops.compose('stop','bot')
        result['outcome']='OBSERVING_WITHOUT_BOT';atomic_json(report,result)
        while now()-started<duration:
            if not marker.exists() or marker.read_text()!=lease:
                result['outcome']='CANCELLED_BY_OPERATOR';break
            try:
                status=ops.napcat_api()('/QQLogin/CheckLoginStatus')
                if type(status.get('isLogin')) is not bool:raise ValueError('Unknown login status')
                failures=0
                result['elapsed_seconds']=round(now()-started)
                if not status['isLogin']:
                    result['outcome']='QQ_OFFLINE_WITHOUT_BOT'
                    snapshot=ops.diagnose(dict(application={'reason':'qq_login_required'},
                        observation={'bot_stopped':True,'elapsed_seconds':result['elapsed_seconds']}))
                    result['incident']=snapshot['incident'];break
            except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError):
                failures+=1
                if failures>=3:
                    result['outcome']='PROBE_UNAVAILABLE';break
            atomic_json(report,result)
            sleep(min(20,max(0,duration-(now()-started))))
        else:result['outcome']='QQ_STILL_ONLINE_WITHOUT_BOT'
    except BaseException:
        result['outcome']='INTERRUPTED';raise
    finally:
        # Only release our own maintenance lease, never another operator's.
        if marker.exists() and marker.read_text()==lease:
            result['bot_resumed']=resume(ops,result,sleep=sleep)
        result['elapsed_seconds']=round(now()-started)
        atomic_json(report,result)
    return {k:v for k,v in result.items() if k!='lease'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='/opt/qqbot')
    parser.add_argument('--duration',type=int,default=9600)
    parser.add_argument('--resume',action='store_true',help='Lease-checked ExecStopPost recovery')
    args=parser.parse_args()
    if not 60<=args.duration<=10800:parser.error('duration must be 60..10800 seconds')
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    if args.resume:
        ops=Operations(args.root);path=ops.state/'qq-isolation.json'
        if path.exists():
            report=json.loads(path.read_text())
            if resume(ops,report):
                report['bot_resumed']=True;atomic_json(path,report)
        raise SystemExit(0)
    print(json.dumps(observe(Operations(args.root),args.duration)))
