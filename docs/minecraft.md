# Minecraft Java server

This backend service runs from `/minecraft`. Defaults are
**26.3**, **Java 25**, **eight player slots**, a **whitelist**, a new survival
world on normal difficulty, and a **2 GB maximum Java heap**. The host has two
CPU cores and approximately 4 GB RAM. The Java process uses more memory than its
heap, so leave room for Ubuntu, NGINX and caching. View distance starts at six
chunks, simulation distance at four.

This is a backend-only service, with no public webpage or HTTP endpoint.
Its filesystem directory is `/minecraft`, outside the website roots. In the
Java client, players use a hostname and optional port, such as
**minecraft.jmporch.com** or **jmporch.com:25565**. The client does not support
`jmporch.com/minecraft`; changing an HTTP redirect or NGINX location cannot
redirect game connections.

For independent routing, point an A record for `minecraft.jmporch.com` to
`23.92.22.231`, then change that record if the game moves. Alternatively,
an SRV record at `_minecraft._tcp.jmporch.com` can target a game hostname and
port so players can enter just `jmporch.com` while the website stays on its own
IP. SRV targets must be hostnames, not IP addresses. Use a DNS-only record if
the DNS provider offers HTTP proxying. DNS changes do not move worlds or save
files; migrate those separately before redirecting players.

## Install on the Ubuntu host

The reviewed deployment bundle contains `minecraft.py`, the installer,
`deploy-minecraft.sh`, this runbook as `minecraft.md`, checksums, and a source
commit marker. For the initial installation, after accepting the EULA:

```sh
sha256sum --check SHA256SUMS
sudo bash ./deploy-minecraft.sh --accept-eula
```

This installs Java and the manager under `/minecraft`, downloads the official
26.3 server, creates the whitelisted `friends` profile, records the acceptance,
starts it, enables boot startup, and permits TCP 25565 if UFW is already active.
It does not create the DNS record or change provider firewall rules. For manual
installation instead, use the steps below.

Copy `scripts/minecraft.py` and `scripts/install-minecraft-manager.sh` to a
temporary directory together. Use the `jporch` administrative account; the
website deployment account cannot install services. Review the files, then:

```sh
sudo apt-get update
sudo apt-get install python3 openjdk-25-jre-headless
sudo bash ./install-minecraft-manager.sh
java -version
sudo minecraftctl create friends --version 26.3
```

The installer creates an unprivileged `minecraft` account and installs the CLI
and systemd unit. It does not download Minecraft, accept the EULA, start the
game, open ports, or enable boot startup. Python 3.12+ is required for restore.
For an alternative Java installation, set its absolute executable path in
`/minecraft/config.json`. The manager checks Mojang's Java requirement.

Read the [Minecraft EULA](https://www.minecraft.net/en-us/eula). After accepting
it yourself, record that decision, start the game, and add actual Java usernames:

```sh
sudo minecraftctl accept-eula friends
sudo minecraftctl start
sudo minecraftctl command 'whitelist add YourMinecraftJavaUsername'
sudo minecraftctl command 'op YourMinecraftJavaUsername'
sudo minecraftctl logs
```

No players or operators are prepopulated. Whitelists belong to each profile;
Mojang authentication remains enabled. Console commands use a local protected
named pipe, with RCON and query disabled. Use `minecraftctl stop`, rather than
the console `stop` command, for an intentional systemd shutdown.

Allow inbound **TCP 25565** through both the host and any provider firewall,
preserving existing SSH/HTTP/HTTPS rules. No extra UDP port is needed for vanilla
Java. Test with a Java 26.3 client from outside the host. The start command waits
up to 180 seconds for a local Minecraft status response; it cannot verify the
public firewall. To start the selected world after a host reboot:

```sh
sudo systemctl enable minecraft.service
```

## Daily management

```sh
sudo minecraftctl list
sudo minecraftctl status
sudo minecraftctl start
sudo minecraftctl stop
sudo minecraftctl restart
sudo minecraftctl logs
sudo minecraftctl command 'list'
sudo minecraftctl command 'whitelist add AnotherUsername'
sudo minecraftctl command 'whitelist remove AnotherUsername'
```

The player cap and Java memory limit are independent. Change either without
editing the launch script:

```sh
sudo minecraftctl settings friends
sudo minecraftctl settings friends --max-players 6 --memory 1536M
```

Changes back up the profile and briefly stop/restart a running service. Monitor
host resources during real play; eight slots do not guarantee good performance
for eight active players on a shared two-core host.

## Separate worlds

Each profile has independent world data, properties, whitelist, operators,
server version and memory settings. Only one profile runs at a time:

```sh
sudo minecraftctl create family --version 26.3
sudo minecraftctl accept-eula family
sudo minecraftctl use family
sudo minecraftctl command 'whitelist add FamilyUsername'
sudo minecraftctl use friends
```

`use` saves and backs up the outgoing profile and starts the selected profile
if the game was running. If it was stopped, `use` only selects the profile;
run `start` afterward. The family whitelist controls access to that world.

## Updates, backup and recovery

Updates are explicit. Mojang's manifest supplies the official stable JAR, size,
checksum and Java requirement. Snapshots are rejected.

```sh
sudo minecraftctl update latest
sudo minecraftctl update 26.3 --profile friends
# Or install a trusted uploaded runnable JAR:
sudo minecraftctl install-jar /tmp/server.jar --version 26.3 --java-major 25 --kind vanilla
```

Downloads are verified before downtime. The manager stops the service, creates
a complete recovery backup, switches the profile's build, and restarts only if
the game was running. Changes to inactive profiles also briefly stop the game
for a consistent backup. Failed backups never activate a new build. A failure
after activation is reported for inspection; worlds are never automatically
downgraded.

```sh
sudo minecraftctl backup
sudo minecraftctl backup family
sudo minecraftctl restore /minecraft/backups/friends/BACKUP.tar.gz friends-recovered
sudo minecraftctl use friends-recovered
```

Backups contain the entire profile **and its exact server JAR**. Restore creates
a new profile and never overwrites an existing world. After an upgrade, restore
the old world and matching build together. Backups briefly stop/restart the game
to save data consistently. Archives are root-readable; do not publish them.

No automatic schedule, deletion or off-host copying is configured. Copy important
backups off the host, periodically test a restore, monitor disk space, and remove
unneeded archives deliberately. Full-profile backups include runtime libraries.
All historical server releases are retained locally.

## Layout and future mods

```text
/minecraft/config.json                 selected profile and Java path
/minecraft/releases/<version>-<hash>/  JAR and release metadata
/minecraft/profiles/friends/           profile.json and data/
/minecraft/profiles/family/            profile.json and data/
/minecraft/backups/<profile>/          version-matched recovery archives
```

Manager code and metadata are root-owned; game data is writable by the dedicated
`minecraft` user. The systemd service restricts filesystem writes. All files are
outside `/srv/www/main`, so website deployments cannot replace worlds or builds.

Vanilla is the initial engine. `install-jar` supports trusted engines sharing
the `java -jar server.jar nogui` launch contract, such as Paper. This is not a
universal modpack installer: Fabric/Forge/NeoForge may need additional files and
launch arguments. Before adding mods, clone a backup into a separate profile,
choose a loader compatible with the Minecraft version, and track exact mod
filenames, versions, checksums and configuration together. Never try to convert
a modded world back to vanilla by simply swapping its JAR.

For a custom engine, omit `--kind vanilla` when installing its JAR. Such profiles
reject official vanilla updates to prevent accidental engine replacement. Update
them with another compatible custom JAR, or restore a vanilla backup into a new
profile to return to vanilla.

For a future reproducible mod setup, prefer a versioned Modrinth `.mrpack`
manifest alongside each modded profile. Its index records Minecraft and loader
versions, file hashes and download URLs, and distinguishes client-only and
server files. It also supports server configuration overrides. This provides a
cleaner deployment input than an untracked folder of JARs; importing that format
would be a separate manager feature. See the
[Modrinth format specification](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)
and [Fabric's compatibility guidance](https://docs.fabricmc.net/players/installing-mods).

References: [server downloads](https://www.minecraft.net/en-us/download/server),
[26.3 release](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3),
[Java 25 requirement](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1).

## Validation

```sh
python3 -m unittest discover -s tests -p 'test_minecraft.py' -v
bash -n scripts/install-minecraft-manager.sh
python3 scripts/minecraft.py --help
```

Tests simulate service control and use fixture JARs. A real Java startup, systemd
validation and an external game connection must be checked on Linux at install
time. The macOS development host has neither systemd nor a Java runtime.
