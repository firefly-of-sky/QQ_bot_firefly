"""Read a NapCat program's action enum; never execute or inspect credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def inspect_bundle(text):
    marker=re.search(r'(?m)^\s*SendPrivateMsg:\s*"send_private_msg"',text)
    if not marker:raise ValueError('NapCat action enum was not found')
    starts=list(re.finditer(r'(?m)^(?:const|var|let) \w+ = \{\s*$',text[:marker.start()]))
    if not starts:raise ValueError('NapCat action enum boundary was not found')
    start=starts[-1].start();end=text.find('\n};',marker.end())
    if end<0:raise ValueError('NapCat action enum is incomplete')
    block=text[start:end]
    actions=sorted(set(re.findall(r'(?m)^\s*[A-Za-z]\w*:\s*"([A-Za-z_.][A-Za-z0-9_.]*)"\s*,?\s*$',block)))
    if not 40<=len(actions)<=1000 or not {'get_login_info','get_status','send_private_msg'}<=set(actions):
        raise ValueError('Unexpected NapCat action registry')
    return dict(provider='napcat',evidence='registered_in_installed_program_not_live_QQ_permission',
                bundle_sha256=hashlib.sha256(text.encode()).hexdigest(),api_actions=actions)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('bundle');p.add_argument('--output',required=True)
    args=p.parse_args();result=inspect_bundle(Path(args.bundle).read_text())
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'registered_actions':len(result['api_actions']),'bundle_sha256':result['bundle_sha256']}))
