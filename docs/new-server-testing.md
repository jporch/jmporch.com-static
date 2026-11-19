# Parallel production deployment during migration

The old server is `172.104.215.71`, serving `https://jmporch.com`. The new Ubuntu
server is `23.92.22.231`, serving `https://test.jmporch.com`.
The main site uses `/srv/www/main/current`, a symlink to a complete release under
`/srv/www/main/releases/`. Haywire has its own root, `/srv/www/haywire/current`.
The blog remains part of the main Zola build.

The existing `Build and deploy production` workflow builds once for
`https://jmporch.com`, then deploys that exact checked artifact to **both the old
and new servers**. Both targets use the existing **production** GitHub environment
and the `jmporch-deploy` account. A separate test environment or deployment workflow
is not needed. Staging remains on GitHub Pages.

Each deployment job checks its own server by fixed IP while retaining the real
HTTPS hostname and certificate verification. The old server is checked as
`jmporch.com`; the new server is checked as `test.jmporch.com` until cutover. Both
must serve the same commit, release marker, and checked file contents. The new
server also checks the independent Haywire route and its noindex header.

The two target jobs run independently with fail-fast disabled: a failure on one
does not cancel the other, but the overall workflow succeeds only if both pass.
There is no transaction or automatic rollback across the two servers.

## 1. Create the deployment account on the server

Run as `jporch`:

```sh
id jmporch-deploy || sudo adduser --disabled-password --gecos "" jmporch-deploy
sudo chown -R jmporch-deploy:jmporch-deploy /srv/www/main
sudo install -d -o jmporch-deploy -g jmporch-deploy -m 0700 \
    /home/jmporch-deploy/.ssh
```

Do not add this account to `sudo` or give it ownership of `/srv/www`, Haywire,
NGINX configuration, or `jporch`'s home. The account can upload and activate the
main site's releases without sudo. `jporch` continues administering everything
through sudo; direct manual uploads as `jporch` will no longer own this site's
deployment directory.

## 2. Use the existing deployment key on the Mac

The key `~/.ssh/jmporch-ventures-deploy` has been verified to authenticate as
`jmporch-deploy` on the new server, with write access to the main deployment
directories. Reuse it. Its fingerprint is
`SHA256:wEMuJ8KJlUxhcjrJRMPC2K9hbphmjcrLlcr5O4NYE68`.

To display its public key for installation on a fresh server:

```sh
ssh-keygen -y -f ~/.ssh/jmporch-ventures-deploy
```

The public key is already installed on `23.92.22.231`. For a fresh server, install
the **public** line prefixed by `restrict`, using this command as `jporch`:

```sh
printf '%s\n' 'restrict ssh-ed25519 REPLACE_WITH_PUBLIC_KEY deployment-key' \
    | sudo tee -a /home/jmporch-deploy/.ssh/authorized_keys >/dev/null
sudo chown jmporch-deploy:jmporch-deploy \
    /home/jmporch-deploy/.ssh/authorized_keys
sudo chmod 0600 /home/jmporch-deploy/.ssh/authorized_keys
```

Replace the entire `ssh-ed25519 ...` portion with the generated public line.
`restrict` disables forwarding and PTY allocation while allowing the noninteractive
SSH commands and rsync needed for deployment. The account still has a shell under
its own unprivileged identity.

From the Mac, check the account and application permissions:

```sh
ssh -T -o IdentitiesOnly=yes -i ~/.ssh/jmporch-ventures-deploy \
    jmporch-deploy@23.92.22.231 \
    'id; test -w /srv/www/main && test -w /srv/www/main/releases && echo "Main deployment writable"; if test -w /srv/www/haywire; then echo "ERROR: Haywire is writable"; exit 1; fi'
```

## 3. Pin the server's SSH identity

On the server, through your already trusted administrative connection:

```sh
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
sudo cat /etc/ssh/ssh_host_ed25519_key.pub
```

On the Mac:

```sh
ssh-keyscan -t ed25519 23.92.22.231 > /tmp/jmporch-new-known-hosts
ssh-keygen -lf /tmp/jmporch-new-known-hosts
```

Verify the SHA256 fingerprint matches the one shown by the trusted server
connection. Then copy the contents of `/tmp/jmporch-new-known-hosts` for the
environment variable below. Do not trust a scan without checking its fingerprint.
These instructions assume SSH port 22.

## 4. Add the new target to the existing production environment

In repository Settings → Environments → **production**, retain the existing
restriction allowing deployment from `main` only. Keep the old server's
`SSH_PORT`, `SSH_USER`, `SSH_KNOWN_HOSTS`, and `SSH_PRIVATE_KEY` settings.

Set the old target's **SSH_HOST to its fixed IPv4 address**, instead of
`jmporch.com`, before publishing the dual-target workflow. When DNS moves, the old
job must continue reaching the old server. If the existing `SSH_KNOWN_HOSTS` entry
only names `jmporch.com`, add an entry naming the old IP with that same verified
host key. Verify the old host fingerprint through the existing trusted server
connection as in step 3. The public checks also use `SSH_HOST` as the old target's
HTTP connection IP, so it must be an IPv4 address.

Add these **environment variables to the same production environment**:

| Variable | Value |
| --- | --- |
| `NEW_SSH_HOST` | `23.92.22.231` |
| `NEW_SSH_PORT` | `22` |
| `NEW_SSH_USER` | `jmporch-deploy` |
| `NEW_SSH_KNOWN_HOSTS` | Verified contents of `/tmp/jmporch-new-known-hosts` |

Add the environment secret **SSH_PRIVATE_KEY_NEW** with the complete new-server key,
including its BEGIN and END lines. On the Mac, this copies it without printing it:

```sh
pbcopy < ~/.ssh/jmporch-ventures-deploy
```

Paste it directly into GitHub's secret field. Never commit it or paste it into a
chat. The old and new keys can be different while sharing one environment. Keep
the original `SSH_PRIVATE_KEY` for the old target. If you already generated a key
under another filename, use that filename instead of generating another one.

If a `server-test` environment was created from the earlier instructions, this
workflow does not use it. It can be removed after any needed settings are copied
into `production`. Keep the `github-pages` environment for staging.

## 5. Publish and verify the workflow

These changes stay local until publishing is explicitly requested. A normal
staging commit publishes only GitHub Pages; it does not deploy either production
server. Publishing this migration workflow to `main` through the project's
production deployment process activates dual-server deployment immediately.
Configure all connection settings first.

Check **Build and deploy production** in the Actions tab. The build and both
**Deploy and verify (old)** and **Deploy and verify (new)** jobs must pass. Both
download the same artifact. The deployment logs identify the active release.
The two servers' `/deployment.json` responses must contain the same commit and
release.

Once published, one push to `main` or one manual run selecting `main` deploys both
targets. Pull requests run build checks without deployment credentials.

Because both servers receive the canonical production build, links, assets, and
feeds in the HTML refer to `https://jmporch.com`. Browsing `test.jmporch.com` may
therefore load linked resources from the old server before cutover. The automated
checks request those files directly from each target IP and compare the response
bytes with the artifact, so they verify the new server independently of those
absolute links.

## Checks and failure behavior

Before upload, Actions runs the normal content, feed, and integration checks. On
the new server, it uploads to a unique `<commit>-<run ID>-<attempt>` directory and
compares the uploaded files against the checked artifact with rsync checksums. Only then does it acquire
the activation lock and atomically replace the `current` symlink. A failed upload
leaves the previous release active. Incomplete or unused release directories are
retained for inspection; no automatic deletion is enabled. The old server retains
its existing direct synchronization into `/srv/jmporch-deploy/releases/current`.
That transfer is checksum-verified but does not switch the entire site atomically.

Public checks use curl's `--resolve` to pin requests to each target without
disabling TLS verification. They check the exact commit and release marker,
homepage, blog, feed XML, styles, JavaScript, portrait, missing-file 404, and
HTTP-to-HTTPS redirect. Requested main-site files must match the checked artifact
byte for byte. Only the new target requires noindex and the Haywire route,
directory redirect, and archive missing-file 404. Checks retry three times before
failing the target job.

A public-check failure marks the workflow failed but **does not automatically
roll back** the activated release. Inspect it and switch to a known-good release
if necessary. The test site remains public; `noindex` is not access control.

To run the public checks from the Mac:

```sh
python3 scripts/check_deployment.py \
    --base-url https://test.jmporch.com \
    --connect-ip 23.92.22.231 \
    --commit COMMIT_FROM_DEPLOYMENT_JSON \
    --release RELEASE_FROM_DEPLOYMENT_JSON \
    --require-noindex --check-haywire
```

To roll back as `jporch` on the server, replace `KNOWN_GOOD_RELEASE` with a directory
name from `/srv/www/main/releases/` (including `bootstrap` if needed):

```sh
sudo -u jmporch-deploy sh -s -- KNOWN_GOOD_RELEASE <<'SH'
set -eu
cd /srv/www/main
exec 9>.deploy.lock
flock -w 60 9
test -s "releases/$1/index.html"
ln -s "releases/$1" current.rollback
mv -Tf current.rollback current
SH
```

No NGINX reload is required for activation or rollback. Keep the test site's
`X-Robots-Tag: noindex, nofollow` header until the eventual production cutover.

## 6. Cut over and retire the old server

After repeated successful dual deployments:

1. Configure the new server to serve `jmporch.com` and obtain a valid certificate
   before changing DNS, using DNS validation or a securely transferred existing
   certificate. Keep `test.jmporch.com` available during migration.
2. Scope the noindex header to the temporary hostname. Production responses for
   `jmporch.com` must be indexable. Verify the production hostname directly:

   ```sh
   curl --resolve jmporch.com:443:23.92.22.231 -I https://jmporch.com/
   ```

3. Update the production A record to `23.92.22.231`. Update any AAAA record to the
   new server's working IPv6 address or remove it. Test the site and certificate
   renewal, and allow the old DNS TTL to expire. Both deployment jobs still reach
   their original servers because SSH and HTTP verification use fixed IPs.
4. Remove the old deployment matrix entry. Set the remaining target's check URL
   to `https://jmporch.com`, stop requiring noindex, and retain the Haywire checks.
   The new target can keep its `NEW_SSH_*` names until a deliberate cleanup.
5. Publish and verify a new-server-only deployment. Then revoke the old server's
   deployment access and shut it down. Do not retire it while the dual-target
   workflow still requires its job to pass.
