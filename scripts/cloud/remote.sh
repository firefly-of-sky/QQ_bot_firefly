#!/usr/bin/env bash
# The machine-specific SSH key and pinned host key stay outside Git.
set -euo pipefail
cd "$(dirname "$0")/../.."
test -f .deployment/ssh_config || { echo 'Project SSH configuration is missing.' >&2; exit 1; }
case "${1:-status}" in
  status|backup|start|stop|configure-login)
    exec ssh -F .deployment/ssh_config qqbot-cloud "python3 /opt/qqbot/current/scripts/cloud/ops.py ${1:-status}"
    ;;
  webui)
    echo 'Open http://127.0.0.1:16099/webui/ while this terminal stays open.'
    exec ssh -F .deployment/ssh_config -o ExitOnForwardFailure=yes \
      -N -L 127.0.0.1:16099:127.0.0.1:6099 qqbot-cloud
    ;;
  *) echo 'Usage: remote.sh [status|backup|start|stop|configure-login|webui]' >&2; exit 1 ;;
esac
