#!/usr/bin/env bash
# Upload only a checked static build, directly into the one production directory.
set -euo pipefail

source_directory="${1:-public}"
destination=/srv/jmporch-deploy/releases/current
: "${SSH_HOST:?Configure the production SSH_HOST variable}"
: "${SSH_PORT:?Configure the production SSH_PORT variable}"
: "${SSH_USER:?Configure the production SSH_USER variable}"
: "${SSH_KNOWN_HOSTS:?Configure the production SSH_KNOWN_HOSTS variable with the verified host key}"
: "${SSH_PRIVATE_KEY:?Replace the production SSH_PRIVATE_KEY placeholder with the deployment key}"

if [[ ! "$SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] ||
   [[ ! "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] ||
   [[ ! "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] ||
   (( 10#$SSH_PORT < 1 || 10#$SSH_PORT > 65535 )); then
  echo 'Invalid SSH host, username, or port.' >&2
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

umask 077
temporary="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/jmporch-ssh.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$temporary/key"
printf '%s\n' "$SSH_KNOWN_HOSTS" > "$temporary/known_hosts"
unset SSH_PRIVATE_KEY
if ! ssh-keygen -y -P '' -f "$temporary/key" >/dev/null 2>&1; then
  echo 'SSH_PRIVATE_KEY must contain the unencrypted deployment private key, not the placeholder.' >&2
  exit 1
fi
cat > "$temporary/config" <<EOF
Host production-target
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

ssh -F "$temporary/config" production-target 'sh -s' <<'REMOTE'
set -eu
root=/srv/jmporch-deploy/releases
target="$root/current"
if [ "$(id -u)" -eq 0 ]; then
  echo 'Use the unprivileged deployment account, not root.' >&2
  exit 1
fi
if ! command -v rsync >/dev/null; then
  echo 'Install rsync on the server with: sudo apt install rsync' >&2
  exit 1
fi
if [ ! -d "$root" ] || [ "$(readlink -f "$root")" != "$root" ]; then
  echo 'The deployment releases directory must exist and must not resolve through a symlink.' >&2
  exit 1
fi
if [ -L "$target" ]; then
  echo 'The current destination must be a real directory, not a release symlink.' >&2
  exit 1
fi
mkdir -p -m 0755 "$target"
chmod 0755 "$target"
if [ ! -w "$target" ]; then
  echo 'The deployment account cannot write to the current directory.' >&2
  exit 1
fi
REMOTE

printf -v remote_shell 'ssh -F %q' "$temporary/config"
options=(
  --recursive --times --compress --perms --checksum
  --chmod=D755,F644 --delay-updates --delete-delay --no-links
  --rsh="$remote_shell"
)
rsync "${options[@]}" -- "$source_directory/" "production-target:$destination/"
changes="$(rsync "${options[@]}" --dry-run --itemize-changes -- "$source_directory/" "production-target:$destination/")"
if [[ -n "$changes" ]]; then
  echo 'The uploaded files differ from the checked build:' >&2
  printf '%s\n' "$changes" >&2
  exit 1
fi
printf 'Production files verified at %s:%s\n' "$SSH_HOST" "$destination"
