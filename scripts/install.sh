#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

apt-get update
apt-get install -y --no-install-recommends python3-venv python3-pip i2c-tools

if ! getent group garden-logger >/dev/null; then
    groupadd --system garden-logger
fi
if ! id garden-logger >/dev/null 2>&1; then
    useradd --system --gid garden-logger --home-dir /nonexistent --shell /usr/sbin/nologin garden-logger
fi

install -d -o root -g root -m 0755 /opt/garden-logger
cp -a "$repo_dir/." /opt/garden-logger/
python3 -m venv /opt/garden-logger/.venv
/opt/garden-logger/.venv/bin/pip install --disable-pip-version-check /opt/garden-logger

install -d -o root -g garden-logger -m 0750 /etc/garden-logger
if [ ! -e /etc/garden-logger/config.toml ]; then
    install -o root -g garden-logger -m 0640 \
        /opt/garden-logger/config/garden-logger.example.toml \
        /etc/garden-logger/config.toml
fi
if [ ! -e /etc/garden-logger/secrets.env ]; then
    install -o root -g garden-logger -m 0600 \
        /opt/garden-logger/deploy/secrets.env.example \
        /etc/garden-logger/secrets.env
fi

install -o root -g root -m 0644 /opt/garden-logger/deploy/garden-logger.service /etc/systemd/system/
install -o root -g root -m 0644 /opt/garden-logger/deploy/garden-logger.timer /etc/systemd/system/
systemctl daemon-reload

echo "Edit /etc/garden-logger/config.toml and secrets.env, then run:"
echo "  systemctl enable --now garden-logger.timer"
