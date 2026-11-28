#!/usr/bin/env bash
# One-time administrator setup. Leaves both game services stopped/disabled.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'Run this reviewed installer with sudo on the Ubuntu host.' >&2
    exit 1
fi
if [[ $# != 2 || $1 != --login-key ]]; then
    echo 'Usage: install-valheim.sh --login-key PUBLIC_KEY_FILE' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $(uname -m) != x86_64 ]]; then
    echo 'This installer targets the existing x86-64 Ubuntu host.' >&2
    exit 1
fi
login_key=$(realpath -- "$2")
ssh-keygen -lf "$login_key"
for file in valheim.py minecraft.py valheim.md minecraft.md; do
    test -f "$source_dir/$file"
done
if [[ ! -f /minecraft/state/config.json || ! -f /minecraft/manager/minecraft.py ]]; then
    echo 'This installer expects the existing Minecraft operator setup.' >&2
    exit 1
fi
minecraft_override=/etc/systemd/system/minecraft.service.d/20-game-exclusivity.conf
if [[ -e $minecraft_override || -L $minecraft_override ]]; then
    [[ -f $minecraft_override && ! -L $minecraft_override &&
       $(sha256sum -- "$minecraft_override" | cut -d ' ' -f 1) == eb7f3e21eba64636fdf5a57c90e0a2cb546a9fa6d84c015746beba8ac2532c4a ]] || {
        echo 'The Minecraft exclusivity override has local modifications. Review it before installing.' >&2
        exit 1
    }
fi
for directory in /valheim /home/valheim; do
    if [[ -L $directory ]]; then
        echo "Refusing symbolic-link directory: $directory" >&2
        exit 1
    fi
done

# Stop gracefully and back up using the installed managers before replacing code.
systemctl stop minecraft.service
if [[ $(systemctl show -p MainPID --value minecraft.service) != 0 ]]; then
    echo 'Minecraft has not fully stopped; no installation will be attempted.' >&2
    exit 1
fi
/usr/local/bin/minecraftctl backup
if [[ $(systemctl show -p LoadState --value valheim.service) != not-found ]]; then
    systemctl stop valheim.service
    if [[ $(systemctl show -p MainPID --value valheim.service) != 0 ]]; then
        echo 'Valheim has not fully stopped.' >&2
        exit 1
    fi
    if [[ -f /valheim/state/config.json && -x /usr/local/bin/valheimctl ]]; then
        current=$(runuser -u valheim -- /usr/bin/python3 -I -c 'import json; print(json.load(open("/valheim/state/config.json"))["active"] or "")')
        if [[ -n $current ]]; then
            /usr/local/bin/valheimctl backup "$current"
        fi
    fi
fi
systemctl disable minecraft.service
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 ca-certificates lib32gcc-s1 lib32stdc++6 libatomic1 libpulse0 libpulse-dev util-linux

if ! id valheim >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /home/valheim --shell /bin/bash valheim
fi
if [[ $(getent passwd valheim | cut -d: -f6) != /home/valheim || $(getent passwd valheim | cut -d: -f7) != /bin/bash ]]; then
    echo 'An existing valheim account has unexpected login settings; inspect it before continuing.' >&2
    exit 1
fi
install -d -o root -g root -m 755 /valheim /valheim/manager
for directory in profiles releases state tools backups exports; do
    if [[ -L /valheim/$directory ]]; then
        echo "Refusing symbolic-link directory: /valheim/$directory" >&2
        exit 1
    fi
    install -d -o valheim -g valheim -m 750 "/valheim/$directory"
done
install -d -o valheim -g valheim -m 700 /home/valheim
runuser -u valheim -- sh -c 'umask 077; mkdir -p /valheim/state/tmp /valheim/tools/steamcmd'

# Valheim's service and its update command share this root-managed lock.
cat > /etc/tmpfiles.d/valheim-runtime.conf <<'TMPFILES'
d /run/valheim-runtime 0750 root valheim -
f /run/valheim-runtime/active.lock 0660 root valheim -
TMPFILES
systemd-tmpfiles --create /etc/tmpfiles.d/valheim-runtime.conf

history="/minecraft/manager/history/$(date -u +%Y%m%dT%H%M%SZ)-valheim-$$"
install -d -o root -g root -m 700 "$history"
cp /minecraft/manager/minecraft.py "$history/minecraft.py"
install -o root -g root -m 755 "$source_dir/minecraft.py" /minecraft/manager/minecraft.py
install -o root -g root -m 755 "$source_dir/valheim.py" /valheim/manager/valheim.py
install -o root -g root -m 644 "$source_dir/valheim.md" /valheim/manager/README.md
install -o root -g root -m 644 "$source_dir/minecraft.md" /minecraft/manager/README.md
cat > /usr/local/bin/valheimctl <<'WRAPPER'
#!/bin/sh
exec /usr/bin/python3 -I /valheim/manager/valheim.py "$@"
WRAPPER
chmod 755 /usr/local/bin/valheimctl

policy=$(mktemp /etc/sudoers.d/.valheim-manager.XXXXXX)
trap 'rm -f "$policy"' EXIT
cat > "$policy" <<'SUDOERS'
valheim ALL=(root) NOPASSWD: /usr/bin/systemctl start valheim.service, /usr/bin/systemctl stop valheim.service, /usr/bin/systemctl restart valheim.service, /usr/bin/journalctl -u valheim.service -n 100 --no-pager
SUDOERS
chmod 440 "$policy"
visudo -cf "$policy"
install -o root -g root -m 440 "$policy" /etc/sudoers.d/valheim-manager

if [[ -f $minecraft_override ]]; then
    mv -T -- "$minecraft_override" "$history/20-game-exclusivity.conf"
fi
cat > /etc/systemd/system/valheim.service <<'UNIT'
[Unit]
Description=On-demand Valheim dedicated server
After=network-online.target systemd-tmpfiles-setup.service
Wants=network-online.target

[Service]
Type=exec
User=valheim
Group=valheim
ExecStart=/usr/bin/flock -n -F -E 75 /run/valheim-runtime/active.lock /usr/bin/python3 -I /valheim/manager/valheim.py run
RuntimeDirectory=valheim
RuntimeDirectoryMode=0750
KillSignal=SIGINT
TimeoutStopSec=180
SendSIGKILL=no
Restart=no
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/valheim/profiles /run/valheim-runtime/active.lock
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl disable valheim.service

runuser -u valheim -- /usr/bin/python3 -I -c '
import pathlib, sys
key=sys.stdin.read().strip()
if not key or "\n" in key or "PRIVATE KEY" in key:
    raise SystemExit("Supply one SSH public-key entry.")
directory=pathlib.Path.home()/".ssh"
directory.mkdir(mode=0o700,exist_ok=True)
directory.chmod(0o700)
path=directory/"authorized_keys"
existing=path.read_text().splitlines() if path.exists() else []
if key not in existing:
    with path.open("a") as stream:
        stream.write("\n".join(["",key,""]))
path.chmod(0o600)
' < "$login_key"
runuser -u valheim -- /usr/local/bin/valheimctl init
if [[ ! -f /valheim/state/setup-complete ]]; then
    # Downloads are unprivileged and occupy the shared slot, so no game can
    # race the SteamCMD installation on this small host.
    runuser -u valheim -- /usr/local/bin/valheimctl update
    current=$(runuser -u valheim -- /usr/bin/python3 -I -c 'import json; print(json.load(open("/valheim/state/config.json"))["active"] or "")')
    if [[ -z $current ]]; then
        runuser -u valheim -- /usr/local/bin/valheimctl generate friends
    fi
fi
if command -v ufw >/dev/null; then
    ufw_state=$(ufw status)
    if [[ $ufw_state == *'Status: active'* ]]; then
        ufw allow 2456:2457/udp comment 'Valheim Steam'
    else
        echo 'UFW is not active; the installer has not enabled or reset it.'
    fi
fi
if [[ -f "$source_dir/source-manifest.json" ]]; then
    install -o root -g root -m 644 "$source_dir/source-manifest.json" /valheim/manager/source-manifest.json
    install -o root -g root -m 644 "$source_dir/source-manifest.json" /minecraft/manager/source-manifest.json
fi
runuser -u valheim -- sh -c 'umask 077; touch /valheim/state/setup-complete'
runuser -u valheim -- /usr/local/bin/valheimctl worlds
echo 'Setup complete. Minecraft and Valheim are both stopped and disabled at boot.'
echo 'SSH: valheim@valheim.jmporch.com; start manually with valheimctl start.'
echo 'Read your private join password with valheimctl password.'
