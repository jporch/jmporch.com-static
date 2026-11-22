#!/usr/bin/env python3
"""Small, single-instance Minecraft manager. Python standard library only."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
import zipfile


MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
SERVICE = 'minecraft.service'
CONSOLE = Path('/run/minecraft/console')
DEFAULT_VERSION = '26.3'
SYSTEMCTL = '/usr/bin/systemctl'
JOURNALCTL = '/usr/bin/journalctl'
SUDO = '/usr/bin/sudo'


def authorize_operator():
    account = pwd.getpwnam('minecraft')
    if os.geteuid() == 0:
        # Never interpret operator-writable paths or launch Java as root.
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    if os.geteuid() != account.pw_uid:
        raise ValueError('Log in as minecraft, or use sudo -iu minecraft.')


def service_control(action):
    if action not in ('start', 'stop', 'restart'):
        raise ValueError('Unsupported privileged service action.')
    return [SUDO, '-n', '--', SYSTEMCTL, action, SERVICE]


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}', value):
        raise ValueError('Names must contain only letters, digits, dots, underscores or hyphens.')
    return value


def memory(value):
    if not re.fullmatch(r'[1-9][0-9]*[MG]', value):
        raise ValueError('Memory must look like 1536M or 2G.')
    if memory_mb(value) < 512:
        raise ValueError('Use at least 512M for the maximum Java heap.')
    return value


def memory_mb(value):
    return int(value[:-1]) * (1024 if value.endswith('G') else 1)


def seed_number(value):
    seed = int(value)
    if not -(2 ** 63) <= seed < 2 ** 63:
        raise ValueError('Seed must be a signed 64-bit integer.')
    return seed


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def fetch_json(url):
    if not url.startswith('https://'):
        raise ValueError('Downloads require HTTPS.')
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def digest(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def check_jar(path):
    with zipfile.ZipFile(path) as archive:
        if 'META-INF/MANIFEST.MF' not in archive.namelist():
            raise ValueError('Not a runnable server JAR (missing manifest).')
        if archive.testzip():
            raise ValueError('Corrupt server JAR.')


def java_major(java):
    result = subprocess.run([java, '-version'], capture_output=True, text=True, check=True)
    match = re.search(r'version "(\d+)', result.stderr + result.stdout)
    if not match:
        raise ValueError('Could not determine the installed Java version.')
    return int(match[1])


class Manager:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @property
    def config_path(self):
        return self.root / 'state/config.json'

    @property
    def config(self):
        return read_json(self.config_path)

    @property
    def work_path(self):
        path = self.root / 'state/tmp'
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        return path

    def profile_path(self, name):
        return self.root / 'profiles' / identifier(name)

    def profile(self, name):
        return read_json(self.profile_path(name) / 'profile.json')

    @contextmanager
    def lock(self):
        # One operator-owned lock covers all CLI mutations, including start/stop.
        with (self.root / 'state/.manager.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def service(self, action):
        command = ([SYSTEMCTL, '--no-pager', 'status', SERVICE] if action == 'status'
                   else service_control(action))
        return subprocess.run(command, check=True)

    def logs(self):
        subprocess.run([SUDO, '-n', '--', JOURNALCTL, '-u', SERVICE,
                        '-n', '100', '--no-pager'], check=True)

    def active(self):
        return subprocess.run([SYSTEMCTL, 'is-active', '--quiet', SERVICE],
                              check=False).returncode == 0

    def stopped(self):
        output = subprocess.check_output([SYSTEMCTL, 'show', '-p', 'ActiveState',
                                          '-p', 'MainPID', '-p', 'ControlPID', SERVICE], text=True)
        state = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if (state.get('ActiveState') not in ('inactive', 'failed')
                or state.get('MainPID') != '0' or state.get('ControlPID') != '0'):
            raise ValueError('Service has not fully stopped; refusing to change live world files.')

    def own_data(self, path):
        account = pwd.getpwnam('minecraft')
        for item in [path, *path.rglob('*')]:
            if item.is_symlink():
                raise ValueError(f'Symlinks are not supported in world profiles: {item}')
            if item.stat().st_uid != account.pw_uid:
                raise ValueError(f'World data must be owned by minecraft: {item}. Rerun the administrator installer to migrate permissions.')

    def store_jar(self, source, version, major=25, kind='vanilla'):
        identifier(version)
        if major < 17:
            raise ValueError('Use a supported Java major version (17 or newer).')
        check_jar(source)
        sha256 = digest(source)
        release = f'{version[:40]}-{sha256}'
        target = self.root / 'releases' / release
        target.mkdir(parents=True, exist_ok=True)
        jar = target / 'server.jar'
        if jar.exists():
            if digest(jar) != sha256:
                raise ValueError('Stored release checksum mismatch.')
        else:
            shutil.copyfile(source, jar)
            jar.chmod(0o644)
        metadata = {'id': release, 'version': version, 'java_major': major,
                    'sha256': sha256, 'kind': kind}
        write_json(target / 'release.json', metadata)
        return release

    def download(self, version):
        manifest = fetch_json(MANIFEST)
        version = manifest['latest']['release'] if version == 'latest' else version
        entry = next((v for v in manifest['versions']
                      if v['id'] == version and v['type'] == 'release'), None)
        if entry is None:
            raise ValueError(f'No official stable release found: {version}')
        details = fetch_json(entry['url'])
        server = details['downloads']['server']
        if not server['url'].startswith('https://'):
            raise ValueError('Downloads require HTTPS.')
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.work_path) as temporary:
            jar = Path(temporary) / 'server.jar'
            with urllib.request.urlopen(server['url'], timeout=60) as response, jar.open('wb') as output:
                shutil.copyfileobj(response, output)
            if jar.stat().st_size != server['size'] or digest(jar, 'sha1') != server['sha1']:
                raise ValueError('Official server download checksum/size mismatch.')
            return self.store_jar(jar, version, details['javaVersion']['majorVersion'])

    def release(self, release):
        return read_json(self.root / 'releases' / identifier(release) / 'release.json')

    def validate_start(self, name):
        profile = self.profile(name)
        release = self.release(profile['release'])
        jar = self.root / 'releases' / release['id'] / 'server.jar'
        if digest(jar) != release['sha256']:
            raise ValueError('Server JAR checksum mismatch; refusing to start.')
        if java_major(self.config['java']) < release['java_major']:
            raise ValueError(f"Minecraft {release['version']} requires Java {release['java_major']}+.")
        if not profile['eula']:
            raise ValueError(f'Read the Minecraft EULA, then run: minecraftctl accept-eula {name}')
        memory(profile['memory'])
        if not isinstance(profile['max_players'], int) or not 1 <= profile['max_players'] <= 100:
            raise ValueError('Player limit must be between 1 and 100.')
        return profile, jar

    def create(self, name, version):
        target = self.profile_path(name)
        if target.exists():
            raise ValueError(f'Profile already exists: {name}')
        release = self.download(version)
        data = target / 'data'
        data.mkdir(parents=True)
        profile = {'release': release, 'memory': '2G', 'max_players': 8, 'eula': False}
        write_json(target / 'profile.json', profile)
        (data / 'server.properties').write_text(
            f'motd=jmporch.com - {name}\nserver-port=25565\nlevel-name=world\n'
            'max-players=8\nwhite-list=true\nenforce-whitelist=true\nonline-mode=true\n'
            'gamemode=survival\ndifficulty=normal\nview-distance=6\nsimulation-distance=4\n'
            'enable-rcon=false\nenable-query=false\n')
        (data / 'eula.txt').write_text('eula=false\n')
        self.own_data(data)
        config = self.config
        if config['active'] is None:
            config['active'] = name
            write_json(self.config_path, config)

    def start(self):
        name = self.config['active']
        if not name:
            raise ValueError('Create a world profile first.')
        self.validate_start(name)
        self.service('start')
        self.wait_ready()

    def wait_ready(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if not self.active():
                raise ValueError('Minecraft exited during startup; run minecraftctl logs.')
            try:
                # Status protocol works without an authenticated game client.
                with socket.create_connection(('127.0.0.1', 25565), timeout=2) as stream:
                    stream.settimeout(2)
                    handshake = b'\x00\xff\xff\xff\xff\x0f\x09localhost' + struct.pack('>H', 25565) + b'\x01'
                    stream.sendall(bytes([len(handshake)]) + handshake + b'\x01\x00')
                    size = read_varint(stream)
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError('Invalid Minecraft status response size.')
                    if read_varint(stream) != 0:
                        raise ValueError('Invalid Minecraft status packet.')
                    length = read_varint(stream)
                    if not 0 < length < size:
                        raise ValueError('Invalid Minecraft status JSON length.')
                    payload = bytearray()
                    while len(payload) < length:
                        chunk = stream.recv(length - len(payload))
                        if not chunk:
                            raise OSError('Incomplete status response.')
                        payload.extend(chunk)
                    status = json.loads(payload)
                    if 'version' in status and 'players' in status:
                        print('Minecraft is ready on TCP 25565.')
                        return
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise ValueError('Startup not confirmed after 180 seconds; inspect logs/status. Service may still be running.')

    def archive(self, name):
        # Only the selected profile can be written by the single game process.
        # An inactive world can be backed up/deleted without interrupting play.
        if name == self.config['active']:
            self.stopped()
        profile = self.profile(name)
        release_dir = self.root / 'releases' / profile['release']
        source = self.profile_path(name)
        for item in source.rglob('*'):
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise ValueError(f'Unsupported file in backup: {item}')
        backups = self.root / 'backups' / name
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = backups / f'{stamp}-{uuid.uuid4().hex[:8]}.tar.gz'
        temporary = target.with_suffix('.partial')
        try:
            with tarfile.open(temporary, 'w:gz') as archive:
                archive.add(source, arcname='profile')
                archive.add(release_dir / 'server.jar', arcname='server.jar')
                archive.add(release_dir / 'release.json', arcname='release.json')
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'Backup: {target}')
        return target

    def backup(self, name):
        running = self.active()
        if running:
            self.service('stop')
        try:
            return self.archive(name)
        finally:
            if running:
                self.start()

    def change(self, name, edit):
        running = self.active()
        if running:
            self.service('stop')
        try:
            backup = self.archive(name)
        except Exception:
            if running:
                self.start()
            raise
        # After this point failures leave the service stopped for inspection.
        # Never downgrade a world automatically after an attempted upgrade.
        edit()
        if running:
            self.start()
        print(f'Change saved. Recovery backup: {backup}')

    def use(self, name):
        self.profile(name)
        if name == self.config['active']:
            print(f'Already selected: {name}')
            return
        if self.active():
            self.validate_start(name)
        def select():
            config = self.config
            config['active'] = name
            write_json(self.config_path, config)
        current = self.config['active']
        if current:
            self.change(current, select)
        else:
            select()

    def worlds(self):
        active = self.config['active']
        for path in sorted((self.root / 'profiles').iterdir()):
            if path.is_dir():
                profile = self.profile(path.name)
                version = self.release(profile['release'])['version']
                selected = path.name == active
                seed = f", seed {profile['seed']}" if 'seed' in profile else ''
                print(f"{'*' if selected else ' '} {path.name} "
                      f"({'active' if selected else 'inactive'}): {version}, "
                      f"{profile['max_players']} players, {profile['memory']} heap{seed}")

    def generate(self, name=None, seed=None):
        source = self.config['active']
        if source is None:
            raise ValueError('Create or select a world profile first.')
        profile = self.profile(source)
        if self.release(profile['release'])['kind'] != 'vanilla':
            raise ValueError('Generation currently supports vanilla profiles only; custom engines need their own world preparation.')
        created = datetime.now(timezone.utc)
        if name is None:
            name = f"{source[:80]}-{created.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        target = self.profile_path(name)
        if target.exists():
            raise ValueError(f'World already exists: {name}. Choose a new name.')
        seed = seed_number(seed) if seed is not None else int.from_bytes(os.urandom(8), 'big', signed=True)
        data = self.profile_path(source) / 'data'
        # These are the simple folder names emitted by this manager and Java.
        # Reject escaped/nested world paths before downtime rather than guessing
        # where their datapacks live or reading outside the profile.
        properties = (data / 'server.properties').read_text().splitlines()
        world_name = 'world'
        for line in properties:
            match = re.match(r'^\s*level-name\s*[=:]\s*(.*)$', line)
            if match:
                world_name = match[1]
        identifier(world_name)
        running = self.active()
        if running:
            self.validate_start(source)

        def activate():
            with tempfile.TemporaryDirectory(prefix='.generate-', dir=self.work_path) as temporary:
                staged = Path(temporary) / 'profile'
                fresh = staged / 'data'
                fresh.mkdir(parents=True)
                # Copy configuration and access policy, never an old world's
                # chunks, player inventories, achievements, or runtime cache.
                for filename in ('server.properties', 'whitelist.json', 'ops.json',
                                 'banned-players.json', 'banned-ips.json', 'server-icon.png'):
                    if (data / filename).is_file():
                        shutil.copyfile(data / filename, fresh / filename)
                if (data / 'codeofconduct').is_dir():
                    shutil.copytree(data / 'codeofconduct', fresh / 'codeofconduct')
                packs = data / world_name / 'datapacks'
                if packs.is_dir():
                    shutil.copytree(packs, fresh / 'world/datapacks')
                # Read after shutdown so the game's latest properties are kept.
                lines = (fresh / 'server.properties').read_text().splitlines()
                values = {'level-name': 'world', 'level-seed': str(seed),
                          'motd': f'jmporch.com - {name}'}
                lines = [line for line in lines if not any(
                    re.match(r'^\s*' + re.escape(key) + r'\s*[=:]', line) for key in values)]
                (fresh / 'server.properties').write_text('\n'.join(
                    lines + [f'{key}={value}' for key, value in values.items()]) + '\n')
                (fresh / 'eula.txt').write_text(f"eula={str(profile['eula']).lower()}\n")
                details = dict(profile, seed=seed, source_profile=source,
                               created_at=created.isoformat())
                write_json(staged / 'profile.json', details)
                self.own_data(fresh)
                staged.rename(target)
            config = self.config
            config['active'] = name
            write_json(self.config_path, config)

        try:
            self.change(source, activate)
        except Exception:
            # Preparation can fail before the new profile is selected. The old
            # world is unchanged, so resume it; never undo a selected new world.
            if running and self.config['active'] == source and not self.active():
                self.stopped()
                self.start()
            raise
        print(f'Generated world: {name}; seed: {seed}')
        print(f'Previous world retained. Switch back with: minecraftctl use {source}')
        if not running:
            print('World selected. Run minecraftctl start to generate its terrain.')
        return name

    def delete(self, name):
        target = self.profile_path(name)
        if name == self.config['active']:
            raise ValueError('Cannot delete the active world, even while stopped. Select another world first.')
        if target.is_symlink():
            raise ValueError('World profiles must not be symbolic links.')
        self.profile(name)
        backup = self.archive(name)
        shutil.rmtree(target)
        print(f'Deleted inactive world: {name}')
        print(f'Recovery backup retained: {backup}')

    def update(self, name, release):
        profile = self.profile(name)
        if profile['release'] == release:
            print('Profile already uses this build.')
            return
        if (self.release(profile['release'])['kind'] != 'vanilla'
                and self.release(release)['kind'] == 'vanilla'):
            raise ValueError('Refusing to replace a custom engine with vanilla. Restore a vanilla backup into a new profile instead.')
        if java_major(self.config['java']) < self.release(release)['java_major']:
            raise ValueError('Install the required Java version before updating.')
        def activate():
            profile['release'] = release
            write_json(self.profile_path(name) / 'profile.json', profile)
        self.change(name, activate)

    def restore(self, backup, name):
        target = self.profile_path(name)
        if target.exists():
            raise ValueError('Restore requires a new profile name; existing worlds are never overwritten.')
        with tempfile.TemporaryDirectory(dir=self.work_path) as temporary:
            temporary = Path(temporary)
            with tarfile.open(backup) as archive:
                for entry in archive.getmembers():
                    path = Path(entry.name)
                    if (path.is_absolute() or '..' in path.parts or not (entry.isfile() or entry.isdir())
                            or not path.parts or path.parts[0] not in ('profile', 'server.jar', 'release.json')):
                        raise ValueError('Unsafe backup member.')
                archive.extractall(temporary, filter='data')
            release = read_json(temporary / 'release.json')
            if digest(temporary / 'server.jar') != release['sha256']:
                raise ValueError('Backup server JAR checksum mismatch.')
            profile = read_json(temporary / 'profile/profile.json')
            memory(profile['memory'])
            restored = self.store_jar(temporary / 'server.jar', release['version'],
                                      release['java_major'], release['kind'])
            profile['release'] = restored
            write_json(temporary / 'profile/profile.json', profile)
            self.own_data(temporary / 'profile/data')
            shutil.move(str(temporary / 'profile'), target)
        print(f'Restored as {name}; select it with: minecraftctl use {name}')

    def settings(self, name, maximum, players):
        profile = self.profile(name)
        if maximum is None and players is None:
            print(json.dumps(profile, indent=2))
            return
        if maximum is not None:
            profile['memory'] = memory(maximum)
        if players is not None:
            if not 1 <= players <= 100:
                raise ValueError('Player limit must be between 1 and 100.')
            profile['max_players'] = players
        def save():
            write_json(self.profile_path(name) / 'profile.json', profile)
        self.change(name, save)

    def run(self):
        name = self.config['active']
        profile, jar = self.validate_start(name)
        data = self.profile_path(name) / 'data'
        properties = data / 'server.properties'
        lines = properties.read_text().splitlines()
        # Enforce managed values; preserve all other world-specific settings.
        values = {'max-players': str(profile['max_players']), 'server-port': '25565',
                  'server-ip': '', 'enable-rcon': 'false', 'enable-query': 'false',
                  'enable-status': 'true'}
        lines = [line for line in lines if not any(
            re.match(r'^\s*' + re.escape(key) + r'\s*[=:]', line) for key in values)]
        properties.write_text('\n'.join(lines + [f'{key}={value}' for key, value in values.items()]) + '\n')
        (data / 'eula.txt').write_text('eula=true\n')
        CONSOLE.unlink(missing_ok=True)
        os.mkfifo(CONSOLE, 0o600)
        pipe = os.open(CONSOLE, os.O_RDWR)
        os.dup2(pipe, 0)
        os.close(pipe)
        os.chdir(data)
        os.execv(self.config['java'], [self.config['java'], '-Xms512M',
                 f"-Xmx{profile['memory']}", '-jar', str(jar), 'nogui'])


def read_varint(stream):
    value = 0
    for index in range(5):
        byte = stream.recv(1)
        if not byte:
            raise OSError('Incomplete status packet.')
        value |= (byte[0] & 127) << (index * 7)
        if not byte[0] & 128:
            return value
    raise ValueError('Invalid VarInt.')


def console(command, manager):
    if not command.strip() or '\n' in command or '\r' in command or len(command.encode()) > 4095:
        raise ValueError('Supply one console command, at most 4095 bytes.')
    if not manager.active() or not stat.S_ISFIFO(CONSOLE.stat().st_mode):
        raise ValueError('Minecraft is not running.')
    fd = os.open(CONSOLE, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, (command + '\n').encode())
    finally:
        os.close(fd)
    print('Command sent. Use minecraftctl logs to see the response.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/minecraft'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run', help='Internal service runner; requires the minecraft account.')
    new = commands.add_parser('create', help='Create a new, independent world profile.')
    new.add_argument('name', type=identifier)
    new.add_argument('--version', default=DEFAULT_VERSION)
    for command in ('use', 'accept-eula'):
        commands.add_parser(command).add_argument('name', type=identifier)
    commands.add_parser('worlds', aliases=['list'], help='List world profiles and mark the active world.')
    generate = commands.add_parser('generate', help='Back up the current world and select a fresh vanilla world.')
    generate.add_argument('name', nargs='?', type=identifier, help='New world name; defaults to a unique timestamped name.')
    generate.add_argument('--seed', type=seed_number, help='Signed 64-bit seed; defaults to a new random seed.')
    commands.add_parser('delete', help='Back up and remove an inactive world.').add_argument('name', type=identifier)
    for command in ('start', 'stop', 'restart', 'status', 'logs'):
        commands.add_parser(command)
    commands.add_parser('backup').add_argument('name', nargs='?', type=identifier)
    update = commands.add_parser('update', help='Fetch a stable release; never updates automatically.')
    update.add_argument('version', help='Explicit version, or latest.')
    update.add_argument('--profile', type=identifier)
    jar = commands.add_parser('install-jar', help='Install a local runnable JAR, retaining a recovery backup.')
    jar.add_argument('jar', type=Path)
    jar.add_argument('--version', required=True, type=identifier)
    jar.add_argument('--java-major', type=int, default=25)
    jar.add_argument('--kind', choices=('vanilla', 'custom-jar'), default='custom-jar',
                     help='Use vanilla only for an official Mojang server JAR.')
    jar.add_argument('--profile', type=identifier)
    restore = commands.add_parser('restore', help='Restore a backup into a NEW profile.')
    restore.add_argument('backup', type=Path)
    restore.add_argument('name', type=identifier)
    settings = commands.add_parser('settings')
    settings.add_argument('name', type=identifier)
    settings.add_argument('--memory', type=memory)
    settings.add_argument('--max-players', type=int)
    commands.add_parser('command').add_argument('text')
    args = parser.parse_args()
    authorize_operator()
    manager = Manager(args.root)
    if args.command == 'run':
        manager.run()
        return
    with manager.lock():
        name = manager.config['active']
        if args.command == 'create':
            manager.create(args.name, args.version)
        elif args.command in ('worlds', 'list'):
            manager.worlds()
        elif args.command == 'generate':
            manager.generate(args.name, args.seed)
        elif args.command == 'delete':
            manager.delete(args.name)
        elif args.command == 'use':
            manager.use(args.name)
        elif args.command == 'accept-eula':
            profile = manager.profile(args.name)
            profile['eula'] = True
            write_json(manager.profile_path(args.name) / 'profile.json', profile)
            print('Recorded EULA acceptance for this profile.')
        elif args.command == 'backup':
            manager.backup(args.name or name)
        elif args.command == 'update':
            profile_name = args.profile or name
            profile = manager.profile(profile_name)  # Validate before downloading.
            if manager.release(profile['release'])['kind'] != 'vanilla':
                raise ValueError('This profile uses a custom engine. Update it with install-jar, retaining that engine.')
            manager.update(profile_name, manager.download(args.version))
        elif args.command == 'install-jar':
            profile_name = args.profile or name
            manager.profile(profile_name)
            release = manager.store_jar(args.jar, args.version, args.java_major, args.kind)
            manager.update(profile_name, release)
        elif args.command == 'restore':
            manager.restore(args.backup, args.name)
        elif args.command == 'settings':
            manager.settings(args.name, args.memory, args.max_players)
        elif args.command == 'command':
            console(args.text, manager)
        elif args.command in ('start', 'restart'):
            if args.command == 'restart':
                manager.service('stop')
            manager.start()
        elif args.command in ('stop', 'status'):
            manager.service(args.command)
        elif args.command == 'logs':
            manager.logs()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError,
            zipfile.BadZipFile, tarfile.TarError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
