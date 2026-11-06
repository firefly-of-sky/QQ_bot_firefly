#!/bin/bash
set -euo pipefail
umask 077
ulimit -c 0
export XDG_RUNTIME_DIR=/tmp/runtime-nativeqq
mkdir -p "$XDG_RUNTIME_DIR" /app/.vnc
chown nativeqq:nativeqq "$XDG_RUNTIME_DIR" /app/.vnc
chmod 700 "$XDG_RUNTIME_DIR" /app/.vnc
# The QQ data, hostname, MAC and machine-id are supplied by the experiment setup.
# This entrypoint does not clean the host environment or load any bot module.
exec gosu nativeqq dbus-run-session -- bash -c '
  Xvfb :1 -screen 0 1080x760x24 -ac -nolisten tcp > /tmp/native-xvfb.log 2>&1 &
  sleep 1
  export DBUS_SESSION_BUS_ADDRESS
  printf "%s" "$DBUS_SESSION_BUS_ADDRESS" > /tmp/native-dbus-address
  chmod 644 /tmp/native-dbus-address
  fluxbox > /tmp/native-wm.log 2>&1 &
  x11vnc -display :1 -localhost -nopw -forever -shared -rfbport 5900 -quiet > /tmp/native-vnc.log 2>&1 &
  websockify --web /usr/share/novnc 6081 localhost:5900 > /tmp/native-websockify.log 2>&1 &
  exec /opt/QQ/qq --no-sandbox --disable-gpu --force-renderer-accessibility
'
