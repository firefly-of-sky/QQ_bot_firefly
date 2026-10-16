#!/usr/bin/env python3
"""Create a source release from an explicit allowlist; never package local secrets."""
import argparse
import hashlib
from pathlib import Path
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
FILES = ('Dockerfile', 'requirements.runtime.txt', 'pyproject.toml',
         'compose.cloud.yaml', 'tulpa.example.toml', 'README.md')
DIRECTORIES = ('qqbot', 'vendor', 'scripts/cloud', 'deploy/systemd', 'docs')


def package(destination):
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination/f'qqbot-{version}-source.tar.gz'
    paths = [ROOT/name for name in FILES]
    for name in DIRECTORIES:
        paths.extend(p for p in (ROOT/name).rglob('*') if p.is_file()
                     and not p.is_symlink() and not any(part.startswith('.') or part in
                     ('__pycache__', 'node_modules') for part in p.relative_to(ROOT/name).parts)
                     and p.suffix not in ('.pyc','.pyo'))
    with tarfile.open(target,'w:gz') as archive:
        for path in sorted(set(paths)):
            archive.add(path,arcname=str(path.relative_to(ROOT)),recursive=False)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix+'.sha256').write_text(f'{digest}  {target.name}\n')
    return target,digest


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default=str(ROOT/'.deployment/releases'))
    args=parser.parse_args()
    path,digest=package(args.output)
    print(f'{path}\nsha256={digest}')
