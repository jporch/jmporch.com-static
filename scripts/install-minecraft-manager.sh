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
command -v sudo >/dev/null
command -v visudo >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
if systemctl is-active --quiet minecraft.service; then
    echo 'Stop minecraft.service before replacing the manager.' >&2
    exit 1
fi
if [[ -L /minecraft ]]; then
    echo '/minecraft must not be a symbolic link.' >&2
    exit 1
fi
if ! id minecraft >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /home/minecraft --shell /bin/bash minecraft
else
    if [[ $(getent passwd minecraft | cut -d: -f6) != /home/minecraft ]]; then
        usermod --home /home/minecraft minecraft
    fi
    if [[ $(getent passwd minecraft | cut -d: -f7) != /bin/bash ]]; then
        usermod --shell /bin/bash minecraft
    fi
fi
install -d -o root -g root -m 755 /minecraft /minecraft/manager
for directory in profiles releases backups state; do
    if [[ -L /minecraft/$directory ]]; then
        echo "Refusing to migrate symbolic-link directory: /minecraft/$directory" >&2
        exit 1
    fi
    install -d -o minecraft -g minecraft -m 750 "/minecraft/$directory"
done
if [[ -L /home/minecraft ]]; then
    echo '/home/minecraft must not be a symbolic link.' >&2
    exit 1
fi
install -d -o minecraft -g minecraft -m 700 /home/minecraft
# Descendants of operator-owned directories are prepared without root, too.
runuser -u minecraft -- sh -c 'umask 077; mkdir -p /minecraft/state/tmp; chmod 700 /minecraft/state/tmp'
install -m 755 -o root -g root "$source_dir/minecraft.py" /minecraft/manager/minecraft.py
cat > /usr/local/bin/minecraftctl <<'SH'
#!/bin/sh
exec /usr/bin/python3 -I /minecraft/manager/minecraft.py "$@"
SH
chmod 755 /usr/local/bin/minecraftctl
if [[ -e /minecraft/config.json || -L /minecraft/config.json ]]; then
    if [[ -L /minecraft/config.json || -e /minecraft/state/config.json || -L /minecraft/state/config.json ]]; then
        echo 'Config migration is ambiguous; inspect /minecraft/config.json and /minecraft/state/config.json.' >&2
        exit 1
    fi
    mv /minecraft/config.json /minecraft/state/config.json
fi
if [[ ! -e /minecraft/state/config.json && ! -L /minecraft/state/config.json ]]; then
    # Create as the operator so nested paths can never confer root file access.
    runuser -u minecraft -- sh -c 'umask 077; cat > /minecraft/state/config.json' <<'JSON'
{
  "active": null,
  "java": "/usr/bin/java"
}
JSON
fi
chown -hR minecraft:minecraft /minecraft/profiles /minecraft/releases /minecraft/backups /minecraft/state

# Exact service commands only. The manager and all game/JAR processing stay
# unprivileged; the account receives no general root Python/shell capability.
sudoers_tmp=$(mktemp /etc/sudoers.d/.minecraft-manager.XXXXXX)
trap 'rm -f "$sudoers_tmp"' EXIT
cat > "$sudoers_tmp" <<'SUDOERS'
minecraft ALL=(root) NOPASSWD: /usr/bin/systemctl start minecraft.service, /usr/bin/systemctl stop minecraft.service, /usr/bin/systemctl restart minecraft.service, /usr/bin/journalctl -u minecraft.service -n 100 --no-pager
SUDOERS
chmod 440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -o root -g root -m 440 "$sudoers_tmp" /etc/sudoers.d/minecraft-manager
cat > /etc/systemd/system/minecraft.service <<'UNIT'
[Unit]
Description=Minecraft Java server
After=network.target

[Service]
Type=exec
User=minecraft
Group=minecraft
ExecStart=/usr/bin/python3 -I /minecraft/manager/minecraft.py run
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
echo 'Log in as minecraft to use minecraftctl without sudo; add your SSH public key as described in the runbook.'
echo 'Install Java 25, then follow docs/minecraft.md to create and start a world.'
