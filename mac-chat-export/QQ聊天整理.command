#!/bin/bash
set -eu
TASK_ROOT="$(cd "$(dirname "$0")" && pwd)"
TASK_PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,11))'; then
        TASK_PYTHON="$candidate"
        break
    fi
done
if [ -z "$TASK_PYTHON" ]; then
    echo '需要 Python 3.11 或更新版本。请从 python.org 安装后重新打开。'
    read -r -p '按回车关闭。' _
    exit 1
fi
set +e
"$TASK_PYTHON" "$TASK_ROOT/launch.py" "$@"
TASK_STATUS=$?
if [ "${1:-}" = "--stop" ]; then
    exit "$TASK_STATUS"
fi
read -r -p '运行已结束。按回车关闭。' _
exit "$TASK_STATUS"
