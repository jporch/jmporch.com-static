#!/usr/bin/env bash
# Install the local manager on Ubuntu; does not download/start Minecraft or open ports.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'Run this installer with sudo on the Minecraft host.' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v python3 >/dev/null
command -v systemctl >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
if systemctl is-active --quiet minecraft.service; then
    echo 'Stop minecraft.service before replacing the manager.' >&2
    exit 1
fi
if ! id minecraft >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /minecraft --shell /usr/sbin/nologin minecraft
fi
install -d -m 755 /minecraft/manager /minecraft/profiles /minecraft/releases
install -d -m 700 /minecraft/backups
install -m 755 "$source_dir/minecraft.py" /minecraft/manager/minecraft.py
cat > /usr/local/bin/minecraftctl <<'SH'
#!/bin/sh
exec /usr/bin/python3 /minecraft/manager/minecraft.py "$@"
SH
chmod 755 /usr/local/bin/minecraftctl
if [[ ! -e /minecraft/config.json ]]; then
    cat > /minecraft/config.json <<'JSON'
{
  "active": null,
  "java": "/usr/bin/java"
}
JSON
    chmod 644 /minecraft/config.json
fi
cat > /etc/systemd/system/minecraft.service <<'UNIT'
[Unit]
Description=Minecraft Java server
After=network.target

[Service]
Type=exec
User=minecraft
Group=minecraft
ExecStart=/usr/bin/python3 /minecraft/manager/minecraft.py run
RuntimeDirectory=minecraft
RuntimeDirectoryMode=0750
KillSignal=SIGINT
TimeoutStopSec=180
# Never forcibly kill a world during saving; a hung shutdown needs admin review.
SendSIGKILL=no
Restart=no
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/minecraft/profiles
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
echo 'Manager installed; no game was started and boot startup remains unchanged.'
echo 'Install Java 25, then follow docs/minecraft.md to create and start a world.'
