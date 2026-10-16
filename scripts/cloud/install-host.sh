#!/usr/bin/env bash
# Run on the already authenticated Ubuntu server. No account/firewall reset.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then echo 'Run with sudo on the cloud server.' >&2; exit 1; fi
. /etc/os-release
if [ "$ID" != ubuntu ] || [ "$VERSION_ID" != 22.04 ]; then echo 'This installer is verified for Ubuntu 22.04.' >&2; exit 1; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg python3 rsync
if ! command -v docker >/dev/null; then
  docker_repo=${DOCKER_APT_REPO:-https://download.docker.com/linux/ubuntu}
  case "$docker_repo" in
    https://download.docker.com/linux/ubuntu|https://mirrors.aliyun.com/docker-ce/linux/ubuntu) ;;
    *) echo 'Use the Docker repository or the documented Aliyun mirror.' >&2; exit 1 ;;
  esac
  install -m 0755 -d /etc/apt/keyrings
  curl --retry 3 -fsSL "$docker_repo/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] %s %s stable\n' "$(dpkg --print-architecture)" "$docker_repo" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null
systemctl enable --now docker
install -d -m 0700 /opt/qqbot /opt/qqbot/releases /opt/qqbot/backups /opt/qqbot/run
install -d /opt/qqbot/data/bot /opt/qqbot/data/qq /opt/qqbot/data/napcat /opt/qqbot/data/plugins
chown 10001:10001 /opt/qqbot/data/bot
# A swap file absorbs short spikes; retain an existing swap configuration.
if ! swapon --noheadings --show | read -r _; then
  if [ ! -e /swap-qqbot ]; then
    fallocate -l 2G /swap-qqbot
    chmod 600 /swap-qqbot
    mkswap /swap-qqbot >/dev/null
    swapon /swap-qqbot
    printf '/swap-qqbot none swap sw 0 0\n' >> /etc/fstab
  fi
fi
printf 'host_ready arch=%s\n' "$(uname -m)"
docker version --format '{{.Server.Version}}'
