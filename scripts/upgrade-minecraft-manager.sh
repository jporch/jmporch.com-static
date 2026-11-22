#!/usr/bin/env bash
# Upgrade an existing backend and optionally install one operator public key.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'Run with sudo on the Minecraft host.' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
login_key=''
if [[ $# == 2 && $1 == --login-key ]]; then
    login_key=$(realpath -- "$2")
    ssh-keygen -lf "$login_key"
elif [[ $# != 0 ]]; then
    echo 'Usage: upgrade-minecraft-manager.sh [--login-key PUBLIC_KEY_FILE]' >&2
    exit 1
fi
if [[ ! -f /minecraft/manager/minecraft.py ]]; then
    echo 'No existing manager found. Use the initial deployment installer.' >&2
    exit 1
fi
running=false
if systemctl is-active --quiet minecraft.service; then
    running=true
fi
finish() {
    result=$?
    trap - EXIT
    if [[ $running == true ]] && ! systemctl is-active --quiet minecraft.service; then
        echo 'Attempting to resume the selected world after the upgrade step.' >&2
        systemctl start minecraft.service || echo 'Could not resume Minecraft; inspect systemctl status minecraft.' >&2
    fi
    exit "$result"
}
trap finish EXIT
systemctl stop minecraft.service
if [[ $(systemctl show -p MainPID --value minecraft.service) != 0 ]]; then
    echo 'The game has not fully stopped. No files will be migrated.' >&2
    exit 1
fi
# The installed CLI still understands the existing layout and makes a complete
# world/build backup before any ownership or configuration migration occurs.
/usr/local/bin/minecraftctl backup
history="/minecraft/manager/history/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -o root -g root -m 700 "$history"
cp /minecraft/manager/minecraft.py "$history/minecraft.py"
cp /etc/systemd/system/minecraft.service "$history/minecraft.service"
bash "$source_dir/install-minecraft-manager.sh"
install -o root -g root -m 644 "$source_dir/minecraft.md" /minecraft/manager/README.md
if [[ -f "$source_dir/source-commit.txt" ]]; then
    install -o root -g root -m 644 "$source_dir/source-commit.txt" /minecraft/manager/source-commit.txt
fi
if [[ -n $login_key ]]; then
    # Append as minecraft, so user-controlled home paths never grant root writes.
    runuser -u minecraft -- /usr/bin/python3 -I -c '
import pathlib, sys
key = sys.stdin.read().strip()
if not key or "\n" in key or "PRIVATE KEY" in key:
    raise SystemExit("Supply one SSH public-key entry.")
directory = pathlib.Path.home() / ".ssh"
directory.mkdir(mode=0o700, exist_ok=True)
directory.chmod(0o700)
path = directory / "authorized_keys"
entries = path.read_text().splitlines() if path.exists() else []
if key not in entries:
    with path.open("a") as stream:
        if entries:
            stream.write("\n")
        stream.write(key + "\n")
path.chmod(0o600)
' < "$login_key"
fi
runuser -u minecraft -- /usr/local/bin/minecraftctl worlds
if [[ $running == true ]]; then
    runuser -u minecraft -- /usr/local/bin/minecraftctl start
fi
echo 'Manager upgraded; the selected world and existing backups are retained.'
echo 'SSH: minecraft@minecraft.jmporch.com; commands: minecraftctl worlds / generate / delete NAME.'
