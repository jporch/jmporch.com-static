#!/usr/bin/env bash
# Run as root on the production Ubuntu host. Does not reboot immediately.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo 'Run this script with sudo.' >&2
    exit 1
fi
. /etc/os-release
if [[ ${ID} != ubuntu || $(timedatectl show -p Timezone --value) != Etc/UTC ]]; then
    echo 'This policy expects Ubuntu with the server timezone set to Etc/UTC.' >&2
    exit 1
fi

# Fail on repository errors instead of silently accepting stale package lists.
apt-get -o APT::Update::Error-Mode=any update
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-upgrade \
    unattended-upgrades update-notifier-common needrestart

config=/etc/apt/apt.conf.d/99zz-jmporch-unattended-upgrades
backup=$(mktemp -d /var/backups/jmporch-unattended-upgrades.XXXXXXXX)
if [[ -e "$config" ]]; then
    cp -a "$config" "$backup/"
else
    touch "$backup/config-was-absent"
fi

# Preserve Ubuntu's security-only origin policy, including security dependencies
# from the release pocket and ESM if subscribed. Do not enable third-party repos.
temporary=$(mktemp /etc/apt/apt.conf.d/.jmporch-unattended.XXXXXXXX)
trap 'rm -f "$temporary"' EXIT
cat > "$temporary" <<'APT'
// Local security maintenance policy. The host timezone is UTC.
APT::Periodic::Enable "1";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
// 04:00 Eastern standard time / 05:00 Eastern daylight time.
Unattended-Upgrade::Automatic-Reboot-Time "09:00";
APT
chmod 0644 "$temporary"
chown root:root "$temporary"
mv -f "$temporary" "$config"

apt-config dump > /dev/null
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer

# Prove the policy resolves correctly before applying eligible security updates.
# On error the detailed dry-run log is kept for diagnosis.
dry_run_log="$backup/dry-run.log"
if ! unattended-upgrade --dry-run --debug > "$dry_run_log" 2>&1; then
    tail -n 60 "$dry_run_log" >&2
    echo "Dry run failed; inspect $dry_run_log before continuing." >&2
    exit 1
fi
unattended-upgrade --verbose

echo "Previous local policy and dry-run log: $backup"
apt-config dump | grep -E 'APT::Periodic::|Unattended-Upgrade::Automatic-Reboot'
systemctl list-timers --no-pager --all apt-daily.timer apt-daily-upgrade.timer
if [[ -e /run/reboot-required ]]; then
    cat /run/reboot-required
    shutdown --show
fi
echo 'Security maintenance configured; required reboots occur at 09:00 UTC.'
