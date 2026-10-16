"""Expose the pinned framework's full authenticated terminal administration."""
import os
from pathlib import Path
import sys

from .backend import load_upstream


def main():
    root = Path(os.environ.get('TULPA_ROOT',Path(os.environ.get('DATABASE_PATH','data/chat.sqlite3')).parent/'tulpa'))
    load_upstream(root)
    from chatlocal.terminal_cli import main as upstream_main
    upstream_main()


if __name__=='__main__':
    main()
