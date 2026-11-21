# Production server deployment

Production runs on `23.92.22.231`. The DNS records for `jmporch.com`,
`www.jmporch.com`, `haywiremag.com`, and `www.haywiremag.com` point to this server.
The old server at `172.104.215.71` is no longer a deployment target. Keep it
available through the previous DNS TTL before retiring it; the main site's old
A-record TTL was one hour.

## Applications and hostnames

| Host or path | Content root | Update method |
| --- | --- | --- |
| `jmporch.com`, `www.jmporch.com` | `/srv/www/main/current` | GitHub Actions on main |
| `jmporch.com/haywire/` | `/srv/www/haywire/current` | Independent archive upload |
| `haywiremag.com`, `www.haywiremag.com` | `/srv/www/haywire/current` | Independent archive upload |
| `test.jmporch.com` | Main site plus Haywire path | Optional noindex hostname |

NGINX reads the main site through a release symlink. Haywire has its own release
symlink, so deploying the main site does not delete or replace archive files.
The blog remains part of the main Zola build.

The production virtual hosts are `/etc/nginx/sites-available/jmporch-production`
and `/etc/nginx/sites-available/haywire`, linked from `sites-enabled`.
The original `jmporch` configuration serves only `test.jmporch.com` and retains
its `X-Robots-Tag: noindex, nofollow` header. Production hosts must be indexable.

## GitHub Actions

The `Build and deploy production` workflow builds once for `https://jmporch.com`,
runs content, feed, and integration checks, and uploads the checked artifact.
Only pushes or manual runs on `main` deploy. Pull requests run checks without
deployment credentials. Staging continues deploying to GitHub Pages.

The deployment job uses the existing **production** GitHub environment with its
main-only branch restriction. Its settings retain their migration-era names:

| Setting | Value |
| --- | --- |
| `NEW_SSH_HOST` | `23.92.22.231` |
| `NEW_SSH_PORT` | `22` |
| `NEW_SSH_USER` | `jmporch-deploy` |
| `NEW_SSH_KNOWN_HOSTS` | Verified ED25519 entry for `23.92.22.231` |
| `SSH_PRIVATE_KEY_NEW` (secret) | Dedicated deployment private key |

The old `SSH_HOST`, `SSH_PORT`, `SSH_USER`, `SSH_KNOWN_HOSTS`, and
`SSH_PRIVATE_KEY` settings are no longer referenced. They can be removed after
retirement or retained temporarily for an explicit rollback plan. No separate
test environment is needed.

## Deployment access

`jporch` is the administrative account. The unprivileged `jmporch-deploy` account
owns `/srv/www/main` and `/srv/www/main/releases`. It needs no sudo access and does
not own Haywire, NGINX configuration, or `jporch`'s home.

The Mac key `~/.ssh/jmporch-ventures-deploy` authenticates to this account.
Its fingerprint is `SHA256:wEMuJ8KJlUxhcjrJRMPC2K9hbphmjcrLlcr5O4NYE68`.
The public key is installed in `/home/jmporch-deploy/.ssh/authorized_keys`.
A `restrict` prefix disables forwarding and PTY allocation while allowing
noninteractive SSH commands and rsync under the deployment account.

To test from the Mac:

```sh
ssh -T -o IdentitiesOnly=yes -i ~/.ssh/jmporch-ventures-deploy \
    jmporch-deploy@23.92.22.231 \
    'whoami; test -w /srv/www/main/releases && echo "Deployment access works"'
```

The complete private key is stored only in GitHub's `SSH_PRIVATE_KEY_NEW` secret
and the local key file. Never commit it or paste it into chat. Public server host
keys must be verified through a trusted administrative connection before updating
`NEW_SSH_KNOWN_HOSTS`.

## Release activation and checks

`scripts/deploy-release.sh` uploads into a unique
`/srv/www/main/releases/<commit>-<run ID>-<attempt>` directory. It verifies all
uploaded files with rsync checksums, then takes the activation lock and atomically
replaces `/srv/www/main/current`. Failed uploads leave the previous release active.
Incomplete or unused releases remain available for inspection; automatic cleanup
is not enabled. Activation and rollback do not require an NGINX reload.

`scripts/check_deployment.py` connects to the fixed server IP using curl's
`--resolve`, retaining the production hostname and normal TLS verification. It
checks the exact commit and release marker, compares main-site files with the
artifact, checks the homepage, blog, feed XML, styles, JavaScript, portrait,
Haywire route, directory redirect, missing-file 404s, and HTTP-to-HTTPS redirect.
The production check rejects noindex headers and robots meta tags. Checks retry
three times before failing the job; public-check failure does not automatically
roll back the activated release.

To check an active release from the Mac:

```sh
python3 scripts/check_deployment.py \
    --base-url https://jmporch.com \
    --connect-ip 23.92.22.231 \
    --commit COMMIT_FROM_DEPLOYMENT_JSON \
    --release RELEASE_FROM_DEPLOYMENT_JSON \
    --require-indexable --check-haywire
```

## Rollback

As `jporch` on the server, replace `KNOWN_GOOD_RELEASE` with a directory name from
`/srv/www/main/releases/`:

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

Alternatively, revert the relevant source change on `main` and deploy it through
the normal project process.

## HTTPS and renewal

Certbot manages separate certificates for `jmporch.com` and `haywiremag.com`, each
including its `www` hostname. Both were initially obtained with manual DNS
validation and subsequently reconfigured for automatic NGINX HTTP validation.
The Certbot snap renewal timer is enabled. Keep port 80 reachable for renewal.

For a certificate initially issued with manual DNS validation, explicitly switch
both the plugin and the saved challenge preference:

```sh
sudo /snap/bin/certbot reconfigure \
    --cert-name jmporch.com \
    --authenticator nginx \
    --preferred-challenges http
```

Use `--cert-name haywiremag.com` for the archive certificate. Reconfigure tests the
new settings before saving them. To test renewal of all installed certificates:

```sh
sudo /snap/bin/certbot renew --dry-run
```

Public A records use `23.92.22.231`; neither production domain currently has an
AAAA record. Add IPv6 DNS only after verifying the new server's IPv6 routing,
firewall, and HTTPS. Avoid restoring an IPv6 record pointing to the old server.
