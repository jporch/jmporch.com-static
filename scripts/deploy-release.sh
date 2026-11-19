#!/usr/bin/env bash
# Upload a checked build as a versioned release on the new server.
set -euo pipefail

source_directory="${1:-public}"
root=/srv/www/main
: "${SSH_HOST:?Configure the target SSH_HOST variable}"
: "${SSH_PORT:?Configure the target SSH_PORT variable}"
: "${SSH_USER:?Configure the target SSH_USER variable}"
: "${SSH_KNOWN_HOSTS:?Configure the target SSH_KNOWN_HOSTS variable}"
: "${SSH_PRIVATE_KEY:?Configure the target SSH_PRIVATE_KEY secret}"
: "${DEPLOY_RELEASE:?Supply a unique release ID}"

if [[ ! "$SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] ||
   [[ ! "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] ||
   [[ ! "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] ||
   (( 10#$SSH_PORT < 1 || 10#$SSH_PORT > 65535 )) ||
   [[ ! "$DEPLOY_RELEASE" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$ ]]; then
  echo 'Invalid SSH settings or release ID.' >&2
  exit 1
fi
for file in index.html feed.xml deployment.json; do
  if [[ ! -s "$source_directory/$file" ]]; then
    echo "Refusing to deploy an incomplete build: missing $file." >&2
    exit 1
  fi
done
if find "$source_directory" -type l -print -quit | grep -q .; then
  echo 'Refusing to deploy a build containing symbolic links.' >&2
  exit 1
fi
python3 - "$source_directory/deployment.json" "$DEPLOY_RELEASE" <<'PY'
import json
import sys
from pathlib import Path
marker = json.loads(Path(sys.argv[1]).read_text())
if marker.get('release') != sys.argv[2]:
    raise SystemExit('The build marker does not match the requested release.')
PY

umask 077
temporary="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/jmporch-release-ssh.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$temporary/key"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$temporary/known_hosts"
unset SSH_PRIVATE_KEY
if ! ssh-keygen -y -P '' -f "$temporary/key" >/dev/null 2>&1; then
  echo 'SSH_PRIVATE_KEY must be a valid unencrypted deployment private key.' >&2
  exit 1
fi
cat > "$temporary/config" <<EOF
Host release-target
    HostName $SSH_HOST
    Port $SSH_PORT
    User $SSH_USER
    IdentityFile "$temporary/key"
    IdentityAgent none
    IdentitiesOnly yes
    UserKnownHostsFile "$temporary/known_hosts"
    GlobalKnownHostsFile /dev/null
    HostKeyAlgorithms ssh-ed25519
    StrictHostKeyChecking yes
    BatchMode yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    ForwardAgent no
    ClearAllForwardings yes
    RequestTTY no
    ConnectTimeout 15
    ConnectionAttempts 2
EOF

ssh -F "$temporary/config" release-target "sh -s -- $DEPLOY_RELEASE" <<'REMOTE'
set -eu
root=/srv/www/main
release="$1"
if [ "$(id -u)" -eq 0 ]; then
  echo 'Use the unprivileged deployment account, not root.' >&2
  exit 1
fi
command -v rsync >/dev/null
command -v flock >/dev/null
for directory in "$root" "$root/releases"; do
  if [ ! -d "$directory" ] || [ "$(readlink -f "$directory")" != "$directory" ]; then
    echo 'The application and releases paths must be real directories.' >&2
    exit 1
  fi
done
if [ ! -L "$root/current" ]; then
  echo 'Expected current to be a release symlink.' >&2
  exit 1
fi
case "$(readlink -f "$root/current")" in
  "$root/releases/"*) ;;
  *) echo 'Current must resolve inside this application release directory.' >&2; exit 1 ;;
esac
if [ ! -w "$root" ] || [ ! -w "$root/releases" ]; then
  echo 'The deployment account needs write access to /srv/www/main and its releases.' >&2
  exit 1
fi
# mkdir deliberately rejects a reused release ID. Never modify an active release.
mkdir -m 0755 "$root/releases/$release"
REMOTE

destination="$root/releases/$DEPLOY_RELEASE"
printf -v remote_shell 'ssh -F %q' "$temporary/config"
options=(
  --recursive --times --compress --perms --checksum --no-links
  --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r
  --rsh="$remote_shell"
)
rsync "${options[@]}" -- "$source_directory/" "release-target:$destination/"
changes="$(rsync "${options[@]}" --dry-run --delete --itemize-changes -- \
  "$source_directory/" "release-target:$destination/")"
if [[ -n "$changes" ]]; then
  echo 'The uploaded release differs from the checked build:' >&2
  printf '%s\n' "$changes" >&2
  exit 1
fi

ssh -F "$temporary/config" release-target "sh -s -- $DEPLOY_RELEASE" <<'REMOTE'
set -eu
root=/srv/www/main
release="$1"
exec 9>"$root/.deploy.lock"
flock -w 60 9
test -L "$root/current"
test -s "$root/releases/$release/index.html"
test -s "$root/releases/$release/feed.xml"
test -s "$root/releases/$release/deployment.json"
next_link="$root/.current-$release"
trap 'rm -f "$next_link"' EXIT
ln -s "releases/$release" "$next_link"
mv -Tf "$next_link" "$root/current"
REMOTE
printf 'Activated checked release %s at %s:%s/current\n' "$DEPLOY_RELEASE" "$SSH_HOST" "$root"
