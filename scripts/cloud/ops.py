#!/usr/bin/env python3
"""Cloud operations; no credentials, message bodies or QR tokens in status output."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.request


def execute(args, timeout=30, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Operation failed: '+args[0])
    return result


class Operations:
    def __init__(self, root, run=execute, now=time.time):
        self.root = Path(root).resolve()
        self.run, self.now = run, now
        self.state = self.root/'run'
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)

    def compose(self, *args):
        return self.run(['docker','compose','--project-name','qqbot-cloud',
            '--project-directory',str(self.root),'--env-file',str(self.root/'.env'),
            '--env-file',str(self.root/'current/release.env'),
            '-f',str(self.root/'current/compose.cloud.yaml'),*args])

    def container(self, service):
        # Compose ps --all can include `compose run` acceptance/admin jobs.
        # Only the resident service belongs to the recovery policy.
        ids = self.run(['docker','ps','--all','--quiet',
            '--filter','label=com.docker.compose.project=qqbot-cloud',
            '--filter',f'label=com.docker.compose.service={service}',
            '--filter','label=com.docker.compose.oneoff=False']).stdout.split()
        if not ids:return None
        if len(ids)!=1:raise RuntimeError('Ambiguous resident service')
        cid=ids[0]
        # State contains no container environment or authentication secrets.
        return json.loads(self.run(['docker','inspect','--format','{{json .State}}',cid]).stdout)

    def status(self):
        result={'maintenance':(self.state/'maintenance').exists(),'services':{}}
        for name in ('napcat','bot'):
            state = self.container(name)
            result['services'][name] = dict(state=state.get('Status','unknown'),
                health=state.get('Health',{}).get('Status','not_configured'),
                oom_killed=state.get('OOMKilled',False)) if state else dict(state='missing')
        if result['services']['bot']['state']=='running':
            try:
                result['application']=json.loads(self.compose('exec','-T','bot','python','-m','qqbot.health','--status').stdout)
            except (ValueError,RuntimeError,subprocess.TimeoutExpired):
                result['application']={'live':False,'ready':False,'reason':'probe_unavailable'}
        if result['services']['napcat']['state']=='running' and not result.get('application',{}).get('ready'):
            result['qq_login']=self.qq_login_status()
        disk=shutil.disk_usage(self.root)
        result['disk_used_percent']=round(100*(disk.total-disk.free)/disk.total,1)
        return result

    def napcat_api(self):
        config=json.loads((self.root/'data/napcat/webui.json').read_text())
        token=config.get('token')
        if not isinstance(token,str) or not token:raise RuntimeError('Private API unavailable')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def post(path,body,credential=None):
            headers={'Content-Type':'application/json'}
            if credential:headers['Authorization']='Bearer '+credential
            req=urllib.request.Request('http://127.0.0.1:6099/api'+path,
                json.dumps(body).encode(),headers=headers)
            with opener.open(req,timeout=3) as response:value=json.load(response)
            if not isinstance(value,dict) or value.get('code')!=0:raise ValueError('Private API unavailable')
            return value['data']
        auth=post('/auth/login',{'hash':hashlib.sha256((token+'.napcat').encode()).hexdigest()})
        return lambda path,body=None:post(path,body or {},auth['Credential'])

    def qq_login_status(self):
        """OneBot can disappear on logout; consult the private WebUI separately."""
        try:
            value=self.napcat_api()('/QQLogin/CheckLoginStatus')
            if not isinstance(value,dict):raise ValueError('Private API unavailable')
            phase=value.get('loginPhase')
            if phase not in ('ready','waiting_qrcode','qrcode_scanned','initializing','reconnecting'):
                phase='unknown'
            return dict(available=True,phase=phase,online=value.get('isLogin') is True,
                        offline=value.get('isOffline') is True)
        except (OSError,ValueError,KeyError,TypeError,RuntimeError):
            return dict(available=False)

    def configure_login(self):
        api=self.napcat_api()
        if api('/QQLogin/CheckLoginStatus').get('isLogin') is not True:
            raise RuntimeError('Confirm QQ login before enabling quick login')
        account=str(api('/QQLogin/GetQQLoginInfo').get('uin',''))
        if not account.isdigit():raise RuntimeError('QQ account unavailable')
        api('/QQLogin/SetQuickLoginQQ',{'uin':account})
        return dict(quick_login_configured=True)

    def watchdog(self):
        status = self.status()
        if status['maintenance']:return dict(action='maintenance',**status)
        path=self.state/'watchdog.json'
        saved=json.loads(path.read_text()) if path.exists() else {}
        actions=[]
        for name,state in status['services'].items():
            bad=state['state'] in ('missing','exited','dead') or (name=='bot' and state.get('health')=='unhealthy')
            item=saved.setdefault(name,{'strikes':0,'last_restart':0})
            item['strikes']=item['strikes']+1 if bad else 0
            if item['strikes']>=3 and self.now()-item['last_restart']>=300:
                if state['state']=='missing':self.compose('up','-d','--no-build','--pull','never',name)
                else:self.compose('restart',name)
                item.update(strikes=0,last_restart=self.now())
                actions.append('restart_'+name)
        # An expired QQ login is an operator action, never a restart loop.
        reason=status.get('application',{}).get('reason')
        if reason=='qq_login_required' or status.get('qq_login',{}).get('phase')=='waiting_qrcode':
            actions.append('qq_scan_required')
        if reason=='qq_operator_unavailable':actions.append('qq_operator_unavailable')
        if status['disk_used_percent']>=85:actions.append('disk_space_low')
        atomic_json(path,saved)
        return dict(action=actions or ['none'],**status)

    def backup(self):
        backups=self.root/'backups';backups.mkdir(mode=0o700,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        dest=backups/f'qqbot-{stamp}.tar.gz'
        with tempfile.TemporaryDirectory(prefix='.snapshot-',dir=backups) as temp:
            snapshot=Path(temp)
            for name in ('.env','config.toml','tulpa.toml','personas'):
                source=self.root/name
                if source.is_dir():shutil.copytree(source,snapshot/name)
                elif source.exists():shutil.copy2(source,snapshot/name)
            source=self.root/'data/bot'
            if source.exists():
                for item in source.rglob('*'):
                    if not item.is_file() or item.is_symlink() or item.name.endswith(('-wal','-shm')):continue
                    target=snapshot/'data/bot'/item.relative_to(source)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if item.suffix in ('.sqlite3','.sqlite','.db'):
                        with sqlite3.connect(item) as src, sqlite3.connect(target) as dst:src.backup(dst)
                    else:shutil.copy2(item,target)
            # Keep the OneBot configuration; QQ device sessions are exported
            # separately while NapCat is stopped, not copied inconsistently live.
            config=self.root/'data/napcat'
            if config.exists():shutil.copytree(config,snapshot/'data/napcat')
            (snapshot/'snapshot.json').write_text(json.dumps(dict(created_at=stamp,qq_device_session=False)))
            fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as out, tarfile.open(fileobj=out,mode='w:gz') as archive:
                for item in snapshot.iterdir():archive.add(item,arcname=item.name,recursive=True)
        # Only this tool's completed, named backups are eligible for retention.
        for old in sorted(backups.glob('qqbot-*.tar.gz'))[:-7]:old.unlink()
        return dict(backup=dest.name,bytes=dest.stat().st_size,retained=7,qq_device_session=False)

    def stop(self):
        (self.state/'maintenance').touch(mode=0o600)
        self.compose('stop')
        return dict(maintenance=True)

    def start(self):
        self.compose('up','-d','--no-build','--pull','never')
        (self.state/'maintenance').unlink(missing_ok=True)
        return dict(maintenance=False)


@contextmanager
def lock(path):
    with open(path,'a') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another cloud operation is running') from None
        yield


def atomic_json(path,value):
    temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream)
    os.replace(temp,path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','watchdog','backup','stop','start','configure-login'])
    parser.add_argument('--root',default='/opt/qqbot')
    args=parser.parse_args()
    ops=Operations(args.root)
    try:
        with lock(ops.state/'operations.lock'):
            print(json.dumps(getattr(ops,args.command.replace('-','_'))(),ensure_ascii=False))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(json.dumps(dict(error=type(exc).__name__,operation=args.command,detail='Check service status locally; credentials omitted')))
        raise SystemExit(1)


if __name__=='__main__':main()
