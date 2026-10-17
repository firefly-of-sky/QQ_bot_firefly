#!/usr/bin/env bash
# A release includes pinned local image references in release.env.
set -euo pipefail
root=/opt/qqbot
release=$(realpath "${1:?Supply an extracted release directory}")
case "$release" in /opt/qqbot/releases/*) ;; *) echo 'Release must be under /opt/qqbot/releases' >&2; exit 1 ;; esac
test "$(id -u)" = 0
test -f "$root/.env"
test -f "$release/release.env"
test -f "$release/compose.cloud.yaml"
# Bind-mounted configuration must be readable by the unprivileged container.
chown root:10001 "$root/config.toml" "$root/tulpa.toml"
chmod 0640 "$root/config.toml" "$root/tulpa.toml"
chgrp -R 10001 "$root/personas"
chmod -R g+rX,o-rwx "$root/personas"
exec 9>"$root/run/operations.lock"
flock -n 9
compose() {
  docker compose --project-name qqbot-cloud --project-directory "$root" \
    --env-file "$root/.env" --env-file "$release/release.env" -f "$release/compose.cloud.yaml" "$@"
}
# Validate without printing the expanded secrets, then verify images are local.
compose config --quiet
while read -r image; do docker image inspect "$image" >/dev/null; done < <(compose config --images)
old=$(readlink -f "$root/current" || true)
touch "$root/run/maintenance"
ln -s "$release" "$root/.current-next"
mv -Tf "$root/.current-next" "$root/current"
if ! compose up -d --no-build --pull never; then
  if [ -n "$old" ] && [ -d "$old" ]; then
    ln -s "$old" "$root/.current-next"
    mv -Tf "$root/.current-next" "$root/current"
    release=$old
    compose up -d --no-build --pull never
    rm -f "$root/run/maintenance"
  fi
  echo 'Activation failed; inspect service status.' >&2
  exit 1
fi
install -m 0644 "$release"/deploy/systemd/qqbot-*.service "$release"/deploy/systemd/qqbot-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now qqbot-watchdog.timer qqbot-backup.timer
rm -f "$root/run/maintenance"
printf 'release_started=%s; verify readiness and QQ login separately\n' "$(basename "$release")"
