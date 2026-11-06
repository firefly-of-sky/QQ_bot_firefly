#!/usr/bin/env python3
"""Run the private native desktop probe and save a bounded control sample."""
import json,time,subprocess
from pathlib import Path
try:
 from .ops import atomic_json,lock
 from .observe_native_qq import collect,CONTAINER
except ImportError:
 from ops import atomic_json,lock
 from observe_native_qq import collect,CONTAINER

def sample(root=Path('/opt/qqbot')):
 state=root/'run';probe=dict(checked_at=time.time(),state='unknown',evidence='visual')
 try:
  confirmation=json.loads((state/'native-qq-experiment.json').read_text()).get('native_login_confirmed_at')
  args=['docker','exec','--user','10001',CONTAINER,'python3','/opt/native-observation/probe_native_qq.py']
  if type(confirmation) in (int,float):args+=['--login-confirmed-at',str(confirmation)]
  r=subprocess.run(args,capture_output=True,text=True,timeout=20)
  if r.returncode==0 and len(r.stdout.encode())<32768:
   value=json.loads(r.stdout)
   if not isinstance(value,dict):raise ValueError('Probe must return an object')
   if value.get('state') in ('online','offline','unknown'):probe['state']=value['state']
   probe['scope']='native_qq_gui_display_only'
   indicators=value.get('indicators',{})
   if not isinstance(indicators,dict):indicators={}
   probe['indicators']={k:v for k,v in indicators.items() if
     (k in ('main_navigation','explicit_invalid_dialog') and type(v) is bool) or
     (k in ('green_pixels','status_regions_sampled','status_capture_failures') and type(v) is int and 0<=v<=4096)}
 except (OSError,ValueError,subprocess.SubprocessError):pass
 # A freshly observed native GUI online marker starts the clock, never an old login claim.
 if probe['state']=='online':probe['login_confirmed_at']=probe['checked_at']
 state.mkdir(mode=0o700,parents=True,exist_ok=True)
 with lock(state/'native-qq-observation.lock'):
  atomic_json(state/'native-qq-probe.json',probe)
  return collect(root)
if __name__=='__main__':
 try:print(json.dumps(sample()))
 except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
  print(json.dumps({'operation':'native_sample','error':type(e).__name__,'detail':'Check private sampler state'}));raise SystemExit(1)
