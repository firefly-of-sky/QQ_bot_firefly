#!/usr/bin/env python3
"""Explicitly start/restore a native QQ control, preserving the existing deployment."""
import argparse,hashlib,json,os,shutil,subprocess,time,uuid
from pathlib import Path
try:
 from .ops import Operations,atomic_json,lock
except ImportError:
 from ops import Operations,atomic_json,lock

IMAGE='qqbot-native-qq:3.2.34-53644'
NAME='qqbot-native-qq-1'
def command(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=120)
 if r.returncode:raise RuntimeError('Native experiment operation failed: '+args[0])
 return r.stdout

def start(root):
 root=Path(root);ops=Operations(root);state=root/'run';marker=state/'maintenance';report=state/'native-qq-experiment.json'
 with lock(state/'operations.lock'):
  if marker.exists() or report.exists():raise RuntimeError('Existing experiment/maintenance requires review')
  command(['docker','image','inspect',IMAGE])
  original=json.loads(command(['docker','inspect','qqbot-cloud-napcat-1']))[0]
  identity=command(['docker','exec','qqbot-cloud-napcat-1','cat','/etc/machine-id'])
  hostname=command(['docker','exec','qqbot-cloud-napcat-1','hostname']).strip()
  mac=list(original['NetworkSettings']['Networks'].values())[0]['MacAddress']
  lease='native-qq-'+uuid.uuid4().hex
  result=dict(phase='PREPARING',started_at=time.time(),previous_release=(root/'current').resolve().name,lease=lease,original_napcat_image=original['Image'],backup=ops.backup())
  atomic_json(report,result)
  fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(lease)
  try:
   ops.compose('stop','bot','napcat')
   private=root/'data/native-control';private.mkdir(mode=0o700,exist_ok=False)
   qq=private/'QQ';shutil.copytree(root/'data/qq',qq,symlinks=True)
   # Only the stopped clone's stale Chromium singleton markers are disposable.
   for name in ('SingletonLock','SingletonSocket','SingletonCookie'):
    (qq/name).unlink(missing_ok=True)
   machine=private/'machine-id';machine.write_text(identity);machine.chmod(0o444)
   command(['chown','-hR','10001:10001',str(qq)])
   command(['docker','run','-d','--name',NAME,'--init','--restart','no','--hostname',hostname,
     '--network','qqbot-cloud_default','--mac-address',mac,'--memory','1800m','--cpus','1.25','--pids-limit','512',
     '--shm-size','1g','--ulimit','nofile=65536:65536','--log-driver','local','--log-opt','max-size=10m','--log-opt','max-file=3',
     '-p','127.0.0.1:6081:6081','-v',str(qq)+':/app/.config/QQ','-v',str(machine)+':/etc/machine-id:ro',IMAGE])
   result.update(phase='WAITING_NATIVE_LOGIN',native_image=command(['docker','image','inspect',IMAGE,'--format','{{.Id}}']).strip(),machine_id_sha256=hashlib.sha256(identity.encode()).hexdigest(),qq_version='3.2.34-53644',bot_replies_paused=True)
   atomic_json(report,result)
  except BaseException:
   command(['docker','rm','-f',NAME]) if subprocess.run(['docker','inspect',NAME],capture_output=True).returncode==0 else None
   ops.compose('up','-d','--no-build','--pull','never','napcat','bot')
   if marker.exists() and marker.read_text()==lease:marker.unlink()
   result['phase']='START_FAILED_RESTORED';atomic_json(report,result);raise
 return {k:v for k,v in result.items() if k not in ('lease',)}

def restore(root):
 ops=Operations(root);state=ops.state;report=state/'native-qq-experiment.json';marker=state/'maintenance'
 with lock(state/'operations.lock'):
  data=json.loads(report.read_text())
  if not marker.exists() or marker.read_text()!=data.get('lease'):raise RuntimeError('Experiment lease does not own maintenance')
  command(['systemctl','disable','--now','qqbot-native-observation.timer'])
  observation=state/'native-qq-observation.json'
  if observation.exists():
   summary=json.loads(observation.read_text())
   if summary.get('outcome') in ('WAITING_LOGIN','OBSERVING'):
    summary.update(outcome='CANCELLED_BY_OPERATOR',finished_at=time.time());atomic_json(observation,summary)
  command(['docker','stop',NAME])
  ops.compose('up','-d','--no-build','--pull','never','napcat','bot')
  marker.unlink();data.update(phase='RESTORED',restored_at=time.time());atomic_json(report,data)
 return dict(phase='RESTORED',qq_online_not_assumed=True)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('start','restore'));p.add_argument('--root',default='/opt/qqbot');a=p.parse_args()
 try:print(json.dumps(globals()[a.command](a.root)))
 except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
  print(json.dumps({'error':type(e).__name__,'operation':'native_qq_experiment','detail':'Check private experiment state; account data omitted'}));raise SystemExit(1)
