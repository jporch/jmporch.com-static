#!/usr/bin/env bash
# Initial backend installation. Run on the Ubuntu host after reviewing this bundle.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'Run with sudo on the Minecraft host.' >&2
    exit 1
fi
if [[ ${1:-} != --accept-eula || $# != 1 ]]; then
    echo 'After accepting https://www.minecraft.net/en-us/eula, run with --accept-eula.' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -L /minecraft ]]; then
    echo '/minecraft must be a real backend directory, not a symbolic link.' >&2
    exit 1
fi
if systemctl is-active --quiet minecraft.service; then
    echo 'Minecraft is already running. Use minecraftctl for updates or stop it before reinstalling.' >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 openjdk-25-jre-headless
bash "$source_dir/install-minecraft-manager.sh"
if [[ ! -f /minecraft/profiles/friends/profile.json ]]; then
    minecraftctl create friends --version 26.3
fi
minecraftctl accept-eula friends
minecraftctl use friends
minecraftctl start

# Do not enable or reset a firewall. Add only the game port to an active UFW.
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 25565/tcp comment 'Minecraft Java'
else
    echo 'UFW is not active; verify any host/provider firewall separately.'
fi
systemctl enable minecraft.service
install -m 644 "$source_dir/minecraft.md" /minecraft/manager/README.md
if [[ -f "$source_dir/source-commit.txt" ]]; then
    install -m 644 "$source_dir/source-commit.txt" /minecraft/manager/source-commit.txt
fi
minecraftctl status
echo 'Minecraft backend is running under /minecraft. Add players with minecraftctl command.'
echo 'DNS: minecraft.jmporch.com A 23.92.22.231 (DNS only); gameplay: TCP 25565.'
