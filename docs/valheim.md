# On-demand Valheim and Minecraft

Valheim is a separate Steam-only Linux dedicated server under `/valheim`, managed
by the `valheim` login. It is intended for two to three players, occasionally up
to five, on the existing two-core, 4 GB host. The unmodified game has ten player
slots; this setup does not install a mod to change that limit. Keep the join
password within the intended group, or use the game's permitted-player list.

Both game services are stopped and disabled at boot after installation. Starting
a game manually keeps it running after SSH closes. The website remains available
independently. No public management webpage or game URL path is installed.

## Administrator setup

The staged bundle contains `valheim.py`, the updated `minecraft.py`,
`install-valheim.sh`, this runbook as `valheim.md`, one selected SSH public key,
`source-manifest.json`, and `SHA256SUMS`. From that directory:

```sh
sha256sum --check SHA256SUMS
sudo bash ./install-valheim.sh --login-key ./operator-login.pub
```

The script gracefully stops Minecraft, creates a complete recovery backup with
the currently installed manager, and preserves the active-world selection. It
installs Linux runtime packages, a dedicated `valheim` account, the CLI/service,
and a lock for Valheim's own runtime and updates. Server files are fetched from Steam with SteamCMD as
the unprivileged Valheim user. A new `friends` profile is selected, but its
terrain is not created until the first game start. Existing worlds are not reset.

The script opens **UDP 2456–2457** only if UFW is already active; it never resets
or enables a firewall. In Squarespace, create the A record
`valheim.jmporch.com → 23.92.22.231`. In the attached Linode Cloud Firewall,
allow inbound UDP 2456–2457 and **save and apply** the change. No Minecraft DNS,
HTTP/HTTPS routes or existing firewall rules are replaced.

Installation leaves **both games stopped and disabled at boot**, including
Minecraft if it was previously enabled. On failure the script also leaves games
stopped for inspection. Check the reported error before resuming a game.
Downloads and unit/profile setup are repeatable; existing passwords and profile
data are retained. A completed setup does not redownload builds on rerun; use
the explicit `update` command for that.

## Login and everyday use

```sh
ssh valheim@valheim.jmporch.com
valheimctl password
valheimctl start
valheimctl status
valheimctl logs
valheimctl stop
```

Read the join password privately with `password`; the installer generates it
and stores it with mode 600. Change it while Valheim is stopped using
`valheimctl password --change`, which prompts without putting the password into
shell history. Start uses a native game password argument as required by the
server, so trusted host administrators can inspect it in the process arguments.
Do not publish service logs or complete server configuration files.

In Valheim, use **Join Game → Add Server** with
**`valheim.jmporch.com:2456`** and the password. The server is not advertised in
the public server list by default. No crossplay relay is enabled; players must
use the Steam edition. Game traffic uses UDP 2456; the Steam query uses UDP 2457.

`start` waits for the game's Steam connection and server-opened log messages.
For publicly listed worlds it also checks the Steam server-query response;
unlisted worlds intentionally do not answer those queries. `status` reports
the service and selected world without querying an unlisted server.
This proves local readiness, not that external firewalls or a player's network
allow UDP traffic. First-time world creation can take several minutes. Check
memory and CPU during the first multiplayer session; this host is a trial size,
and larger worlds or bases may need more capacity.

`stop` sends a graceful stop through systemd, waits for the process to exit, and
creates a recovery backup. An intentional stop does not automatically restart.
After reboot, both games remain off until explicitly started.

Minecraft and Valheim can run at the same time. Use each game's own start
and stop commands.

## World profiles

```sh
valheimctl worlds
valheimctl generate
valheimctl generate family
valheimctl use friends
valheimctl settings family
valheimctl delete family
```

`generate` creates a fresh profile using the selected/default installed build.
The game picks the new world's seed on first start. There is no custom-seed
argument or general world-import feature in this first version. An omitted name
becomes a unique timestamped name. Existing profile names are always rejected.

A fresh world carries the source profile's admin, banned and permitted-player
lists, but no terrain or game progress. Creating/switching worlds backs up the
outgoing profile and resumes the selected world only if Valheim was already
running. If it was stopped, the new selection remains stopped. Minecraft need
not be stopped merely to prepare an inactive Valheim world.

`worlds` marks the selected profile with `*` and active/inactive labels even when
the game is stopped. `list` is an alias. `settings NAME` shows the profile's world
name, retained build, visibility, creation time and source profile.

`delete` is only allowed for an inactive profile. It creates a recovery archive
before removing that profile, does not interrupt a different active world, and
keeps the matching server build and archives. The selected profile cannot be
deleted, even while stopped. Select another world first.

Access lists are ordinary files in `/valheim/profiles/NAME/data/`:
`adminlist.txt`, `bannedlist.txt`, and `permittedlist.txt`. Edit them as `valheim`
while the game is stopped. Use the exact, case-sensitive platform user ID shown
in Valheim's F2 panel or the server log. An empty permitted list does not limit
the server to named players; once populated, only listed IDs are permitted.
Server/world settings and passwords are separate from client-side characters.

## Export, backup and recovery

```sh
valheimctl export friends
valheimctl export friends ~/friends.zip
valheimctl backup
valheimctl backup family
valheimctl restore /valheim/backups/friends/BACKUP.tar.gz recovered
valheimctl use recovered
```

`export` makes a **world-only ZIP** under `/valheim/exports` by default and prints
its path. It includes the complete current-format world directory, including
chunks and metadata. Legacy `.db`/`.fwl` saves are exported as a matched pair.
It excludes passwords, server settings, access lists, game binaries and runtime
caches. A destination that already exists is refused.

Download the printed path to your own computer, for example:

```sh
scp valheim@valheim.jmporch.com:/valheim/exports/friends-EXAMPLE.zip .
```

For a local game, extract and place the whole world directory (or the legacy
pair) in that game's `worlds_local` location. Keep all files in a current world
folder together. Exports do not include a player's separate character files.
Exporting the running profile gracefully stops and resumes it to obtain a
consistent save; exporting an inactive profile does not interrupt the game.

Recovery archives include the profile, world data, access lists and exact build
metadata. They omit reproducible runtime caches and logs. Game builds are
retained separately under `/valheim/releases`; archives do not duplicate the
entire Steam installation. Local `restore` requires its matching retained build
and always creates a new profile, preserving existing worlds. For full off-host
disaster recovery, copy retained builds separately as well as world backups.

No scheduled cleanup or off-host copying is installed. Deliberately remove old
exports/backups when no longer needed, and periodically test a restore. Valheim
also maintains its built-in automatic save backups while the game is running.

## Updates and layout

```sh
valheimctl update
```

Updates are explicit and use the stable Steam server app **896660**, anonymously.
Both games must be stopped while SteamCMD installs and validates a staged build;
an update of running Valheim stops it first and resumes it afterward. Downloads
hold the same shared slot used by the game services. An existing world is backed
up before its build changes. Prior builds and backups are retained; a failed new
startup never automatically downgrades a world that may have been upgraded.
Use a matching pre-update backup/build for recovery. Clients must use a compatible
game version. No mods or automatic update jobs are installed.

```text
/valheim/manager/                    root-owned installed manager and runbook
/valheim/state/config.json           selected world, port, default build
/valheim/state/password              private join password
/valheim/tools/steamcmd/             unprivileged SteamCMD and its own cache
/valheim/releases/<build>/           complete versioned game installation
/valheim/profiles/<name>/            profile.json and data/worlds_local/
/valheim/backups/<name>/             private recovery archives
/valheim/exports/                    private world-only ZIPs
```

Manager code, launchers, service units and permission rules remain root-owned.
The operator manages game data without root. Four exact passwordless commands
permit only Valheim's service start/stop/restart and its last 100 journal entries.
Valheim's runtime lock lives at `/run/valheim-runtime/active.lock` and is recreated
by tmpfiles after boot. It protects Valheim updates from a simultaneous Valheim
start. The Valheim service can write its profile directory and runtime lock.
Steam/Unity's runtime home is inside its selected profile, separate from SSH keys.
Administrator operations such as installing packages, changing service units,
firewall changes and managing login keys still require sudo.

References: [official server guide](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/),
[SteamCMD use in LinuxGSM](https://docs.linuxgsm.com/steamcmd),
[Valheim container reference](https://github.com/community-valheim-tools/valheim-server-docker).
