#!/usr/bin/env python3
"""On-demand, Steam-only Valheim manager. Python 3.12+; standard library only."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
import zipfile

SERVICE = 'valheim.service'
SYSTEMCTL = '/usr/bin/systemctl'
SUDO = '/usr/bin/sudo'
RUNTIME_LOCK = Path('/run/valheim-runtime/active.lock')
STEAMCMD_URL = 'https://media.steampowered.com/client/steamcmd_linux.tar.gz'
APP_ID = '896660'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('Names must be 1–64 letters, digits, underscores or hyphens, beginning with a letter or digit.')
    return value


def authorize_operator():
    account = pwd.getpwnam('valheim')
    if os.geteuid() == 0:
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    if os.geteuid() != account.pw_uid:
        raise ValueError('Log in as valheim, or use sudo -iu valheim.')


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    write_private(path, json.dumps(value, indent=2) + '\n')


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            temporary.chmod(0o600)
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unit_state(unit):
    result = subprocess.run([SYSTEMCTL, 'show', '-p', 'ActiveState', '-p', 'MainPID',
                             '-p', 'ControlPID', '-p', 'LoadState', unit],
                            capture_output=True, text=True, check=False)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('LoadState') == 'not-found':
        return {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}
    if result.returncode or not {'ActiveState', 'MainPID', 'ControlPID'} <= values.keys():
        raise ValueError(f'Could not inspect {unit}; refusing to assume it is stopped.')
    return values


def stopped_state(values):
    return (values['ActiveState'] in ('inactive', 'failed')
            and values['MainPID'] == '0' and values['ControlPID'] == '0')


@contextmanager
def reserve_valheim_runtime(path=None):
    # The Valheim service holds the same lock for the lifetime of its process.
    with (path or RUNTIME_LOCK).open('r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Valheim or another Valheim update already holds the runtime lock.') from None
        yield


def query_info(host, port, timeout=2):
    request = b'\xff\xff\xff\xffTSource Engine Query\x00'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.settimeout(timeout)
        stream.connect((host, port))
        stream.send(request)
        packet = stream.recv(65535)
        if packet.startswith(b'\xff\xff\xff\xffA') and len(packet) == 9:
            stream.send(request + packet[5:9])
            packet = stream.recv(65535)
    if not packet.startswith(b'\xff\xff\xff\xffI') or len(packet) < 6:
        raise ValueError('No supported Steam A2S_INFO response received.')
    offset = 6
    def string():
        nonlocal offset
        end = packet.index(b'\x00', offset)
        value = packet[offset:end].decode('utf-8', errors='replace')
        offset = end + 1
        return value
    name, world, folder, game = [string() for _ in range(4)]
    if len(packet) < offset + 9:
        raise ValueError('Truncated Steam query response.')
    offset += 2  # Legacy 16-bit app ID.
    players, maximum = packet[offset:offset + 2]
    offset += 7  # Players, max, bots, server type, OS, visibility and VAC.
    version = string()
    return {'name': name, 'world': world, 'game': game, 'folder': folder,
            'players': players, 'max_players': maximum, 'version': version}


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
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @contextmanager
    def lock(self):
        with (self.root / 'state/.manager.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def initialize(self):
        if not self.config_path.exists():
            write_json(self.config_path, {'active': None, 'default_release': None,
                                         'port': 2456, 'server_name': 'JMPorch Valheim',
                                         'save_interval': 900})
        path = self.root / 'state/password'
        if not path.exists():
            write_private(path, secrets.token_urlsafe(18) + '\n')
        print('Initialized private settings. Use valheimctl password to read the join password.')

    def profile_path(self, name):
        path = self.root / 'profiles' / identifier(name)
        if path.is_symlink():
            raise ValueError('World profile directories must not be symbolic links.')
        return path

    def profile(self, name):
        profile = read_json(self.profile_path(name) / 'profile.json')
        identifier(profile['world'])
        identifier(profile['release'])
        return profile

    def release(self, name):
        return read_json(self.root / 'releases' / identifier(name) / 'release.json')

    def active(self):
        return unit_state(SERVICE)['ActiveState'] == 'active'

    def stopped(self):
        if not stopped_state(unit_state(SERVICE)):
            raise ValueError('Valheim has not fully stopped; refusing to modify live world files.')

    def service(self, action):
        if action not in ('start', 'stop', 'restart'):
            raise ValueError('Unsupported privileged service action.')
        subprocess.run([SUDO, '-n', '--', SYSTEMCTL, action, SERVICE], check=True)

    def label(self, name):
        return self.config['server_name'][:20] + ' - ' + name[:32]

    def validate_start(self):
        config = self.config
        name = config['active']
        if name is None:
            raise ValueError('Generate or select a world first.')
        profile = self.profile(name)
        release = self.release(profile['release'])
        binary = self.root / 'releases' / profile['release'] / 'valheim_server.x86_64'
        if not binary.is_file() or sha256(binary) != release['binary_sha256']:
            raise ValueError('Valheim binary checksum mismatch or missing build. Run valheimctl update.')
        if not 1024 <= config['port'] <= 65534:
            raise ValueError('Port must leave room for both game and query ports (1024–65534).')
        password = (self.root / 'state/password').read_text().strip()
        if len(password) < 5 or '\n' in password or '\r' in password:
            raise ValueError('Set a valid server password of at least five characters.')
        return name, profile, binary, password

    def start(self):
        self.validate_start()
        self.service('start')
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if not self.active():
                raise ValueError('Valheim exited during startup; run valheimctl logs.')
            try:
                name = self.config['active']
                profile = self.profile(name)
                log = self.profile_path(name) / 'data/server.log'
                content = log.read_text(errors='replace').lower() if log.is_file() else ''
                connected = 'game server connected' in content
                if profile['public']:
                    # Unlisted servers intentionally do not answer A2S queries.
                    ready = query_info('127.0.0.1', self.config['port'] + 1)['name'] == self.label(name)
                else:
                    ready = 'opened steam server' in content
                if connected and ready:
                    print(f"Valheim is ready: join directly on UDP {self.config['port']}.")
                    return
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise ValueError('Readiness not confirmed after five minutes. Inspect valheimctl logs; the process may still be running.')

    def stop(self):
        self.service('stop')
        self.stopped()
        if self.config['active'] is not None:
            self.archive(self.config['active'])

    def logs(self):
        subprocess.run([SUDO, '-n', '--', '/usr/bin/journalctl', '-u', SERVICE,
                        '-n', '100', '--no-pager'], check=True)

    def status(self):
        config = self.config
        print(json.dumps({'service': unit_state(SERVICE), 'active_world': config['active'],
                          'port': config['port'], 'query_port': config['port'] + 1,
                          'profile': self.profile(config['active']) if config['active'] else None}, indent=2))
        if self.active() and self.profile(config['active'])['public']:
            print(json.dumps(query_info('127.0.0.1', config['port'] + 1), indent=2))
        elif self.active():
            print('Unlisted server: Steam browser queries are disabled; join directly by hostname or IP.')

    def world_files(self, name):
        data = self.profile_path(name) / 'data/worlds_local'
        world = self.profile(name)['world']
        folder = data / world
        if folder.is_dir() and not folder.is_symlink() and any(path.is_file() for path in folder.rglob('*')):
            return [(folder, world)]
        pair = [(data / (world + suffix), world + suffix) for suffix in ('.db', '.fwl')]
        if all(path.is_file() and not path.is_symlink() for path, _ in pair):
            return pair
        raise ValueError('No saved world found. Start and gracefully stop this world before exporting it.')

    def worlds(self):
        for path in sorted((self.root / 'profiles').iterdir()):
            if path.is_dir():
                profile = self.profile(path.name)
                active = path.name == self.config['active']
                print(f"{'*' if active else ' '} {path.name} ({'active' if active else 'inactive'}): Steam build {self.release(profile['release'])['build_id']}")

    def safe_files(self, base, runtime_exclusions=False):
        def walk(directory):
            for path in sorted(directory.iterdir()):
                relative = path.relative_to(base)
                if runtime_exclusions and (relative.parts[:2] == ('data', '.runtime')
                                           or relative.as_posix() in ('data/server.log', 'data/server.previous.log')):
                    continue
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise ValueError(f'Unsupported file in world snapshot: {relative}')
                yield path
                if path.is_dir():
                    yield from walk(path)
        return walk(base)

    def archive(self, name):
        if name == self.config['active']:
            self.stopped()
        profile = self.profile(name)
        directory = self.root / 'backups' / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = directory / f'{stamp}-{uuid.uuid4().hex[:8]}.tar.gz'
        temporary = target.with_suffix('.partial')
        try:
            with tarfile.open(temporary, 'w:gz') as archive:
                source = self.profile_path(name)
                archive.add(source / 'profile.json', arcname='profile/profile.json')
                for path in self.safe_files(source, runtime_exclusions=True):
                    if path.name == 'profile.json' and path.parent == source:
                        continue
                    archive.add(path, arcname='profile/' + path.relative_to(source).as_posix(), recursive=False)
                metadata = json.dumps(self.release(profile['release'])).encode()
                entry = tarfile.TarInfo('release.json')
                entry.size = len(metadata)
                archive.addfile(entry, io.BytesIO(metadata))
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'Backup: {target}')
        return target

    @contextmanager
    def snapshot(self, name):
        resume = name == self.config['active'] and self.active()
        if resume:
            self.service('stop')
        try:
            if name == self.config['active']:
                self.stopped()
            yield
        finally:
            if resume:
                self.start()

    def backup(self, name):
        with self.snapshot(name):
            return self.archive(name)

    def select(self, name):
        self.profile(name)
        current = self.config['active']
        if name == current:
            return
        resume = self.active()
        if resume:
            self.service('stop')
        try:
            if current is not None:
                self.archive(current)
        except Exception:
            if resume:
                self.start()
            raise
        config = self.config
        config['active'] = name
        write_json(self.config_path, config)
        if resume:
            self.start()

    def generate(self, name=None):
        source = self.config['active']
        release = self.profile(source)['release'] if source else self.config['default_release']
        if release is None:
            raise ValueError('Run valheimctl update to install the server files first.')
        if name is None:
            name = 'world-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        target = self.profile_path(name)
        if target.exists():
            raise ValueError('That world already exists. Choose a new name.')
        self.release(release)
        target.mkdir()
        try:
            (target / 'data').mkdir()
            # Carry access policy across a fresh world, never character/world data.
            for filename in ('adminlist.txt', 'bannedlist.txt', 'permittedlist.txt'):
                previous = self.profile_path(source) / 'data' / filename if source else None
                if previous is not None and previous.is_symlink():
                    raise ValueError('Access lists must not be symbolic links.')
                (target / 'data' / filename).write_text(previous.read_text() if previous and previous.is_file() else '')
            write_json(target / 'profile.json', {'world': name, 'release': release,
                        'public': False, 'source_profile': source,
                        'created_at': datetime.now(timezone.utc).isoformat()})
        except Exception:
            shutil.rmtree(target)
            raise
        self.select(name)
        print(f'New world selected: {name}. Terrain is created on its first start; the previous world is retained.')

    def delete(self, name):
        self.profile(name)
        if name == self.config['active']:
            raise ValueError('Cannot delete the active world, even while stopped. Select another world first.')
        self.archive(name)
        shutil.rmtree(self.profile_path(name))
        print(f'Deleted inactive world {name}; its recovery backup and game build are retained.')

    def export(self, name, destination=None):
        self.profile(name)
        if destination is None:
            destination = self.root / 'exports' / f'{name}-{uuid.uuid4().hex[:8]}.zip'
        destination = Path(destination).expanduser().absolute()
        if destination.exists():
            raise ValueError('Export destination exists; choose a new filename.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('.' + destination.name + '-' + uuid.uuid4().hex)
        try:
            with self.snapshot(name):
                with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                    for source, label in self.world_files(name):
                        if source.is_dir():
                            for path in self.safe_files(source):
                                archive.write(path, label + '/' + path.relative_to(source).as_posix())
                        else:
                            archive.write(source, label)
                temporary.chmod(0o600)
                # Exclusive destination creation prevents even a racing export
                # from overwriting a prior file. Both paths are on one filesystem.
                os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'World-only export: {destination}')
        return destination

    def restore(self, backup, name):
        target = self.profile_path(name)
        if target.exists():
            raise ValueError('Restore requires a new world profile name.')
        with tempfile.TemporaryDirectory(dir=self.work_path) as temporary:
            temporary = Path(temporary)
            with tarfile.open(backup) as archive:
                for item in archive.getmembers():
                    path = Path(item.name)
                    if (path.is_absolute() or '..' in path.parts or not path.parts
                            or path.parts[0] not in ('profile', 'release.json')
                            or not (item.isfile() or item.isdir())):
                        raise ValueError('Unsafe backup entry.')
                archive.extractall(temporary, filter='data')
            profile = read_json(temporary / 'profile/profile.json')
            identifier(profile['world'])
            saved_release = read_json(temporary / 'release.json')
            release = self.release(identifier(profile['release']))
            if release != saved_release:
                raise ValueError('The backup requires its matching retained server build.')
            (temporary / 'profile').rename(target)
        print(f'Restored as {name}; select it with valheimctl use {name}.')

    def install_steamcmd(self):
        tools = self.root / 'tools/steamcmd'
        launcher = tools / 'steamcmd.sh'
        if launcher.is_file():
            return launcher
        tools.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.work_path) as temporary:
            package = Path(temporary) / 'steamcmd.tar.gz'
            with urllib.request.urlopen(STEAMCMD_URL, timeout=60) as response, package.open('wb') as output:
                shutil.copyfileobj(response, output)
            with tarfile.open(package) as archive:
                archive.extractall(tools, filter='data')
            write_json(tools / 'bootstrap.json', {'url': STEAMCMD_URL, 'sha256': sha256(package)})
        if not launcher.is_file():
            raise ValueError('SteamCMD bootstrap did not contain steamcmd.sh.')
        return launcher

    def download_build(self):
        launcher = self.install_steamcmd()
        with tempfile.TemporaryDirectory(dir=self.work_path) as temporary:
            staged = Path(temporary) / 'server'
            staged.mkdir()
            subprocess.run([str(launcher), '+force_install_dir', str(staged), '+login', 'anonymous',
                            '+app_update', APP_ID, 'validate', '+quit'], cwd=launcher.parent, check=True)
            manifest = staged / f'steamapps/appmanifest_{APP_ID}.acf'
            match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text())
            binary = staged / 'valheim_server.x86_64'
            if not match or not binary.is_file() or not (staged / 'linux64').is_dir():
                raise ValueError('SteamCMD returned an incomplete Valheim installation.')
            with binary.open('rb') as stream:
                if stream.read(4) != b'\x7fELF':
                    raise ValueError('SteamCMD did not supply a Linux server executable.')
            build_id = match[1]
            release = 'steam-' + build_id + '-' + uuid.uuid4().hex[:8]
            write_json(staged / 'release.json', {'id': release, 'build_id': build_id,
                       'app_id': APP_ID, 'binary_sha256': sha256(binary),
                       'installed_at': datetime.now(timezone.utc).isoformat()})
            binary.chmod(0o755)
            staged.rename(self.root / 'releases' / release)
        return release

    def update(self):
        source = self.config['active']
        resume = self.active()
        if resume:
            self.service('stop')
        activated = False
        try:
            self.stopped()
            with reserve_valheim_runtime():
                if source is not None:
                    self.archive(source)
                release = self.download_build()
                config = self.config
                config['default_release'] = release
                # Once metadata writes begin, failures leave the service stopped
                # for inspection rather than assuming its old build is selected.
                activated = True
                if source is not None:
                    profile = self.profile(source)
                    profile['release'] = release
                    write_json(self.profile_path(source) / 'profile.json', profile)
                write_json(self.config_path, config)
        except Exception:
            if resume and not activated:
                self.start()
            raise
        if resume:
            self.start()
        print(f'Installed Steam build {self.release(release)["build_id"]}; previous builds and world backups are retained.')

    def password(self, change=False):
        path = self.root / 'state/password'
        if change:
            self.stopped()
            first = getpass.getpass('New Valheim password (at least five characters): ')
            if len(first) < 5 or first != first.strip() or '\n' in first or '\r' in first or first != getpass.getpass('Confirm password: '):
                raise ValueError('Passwords must match and contain at least five characters.')
            write_private(path, first + '\n')
        else:
            print(path.read_text().strip())

    def run(self):
        name, profile, binary, password = self.validate_start()
        data = self.profile_path(name) / 'data'
        runtime = data / '.runtime'
        sdk = runtime / '.steam/sdk64'
        sdk.mkdir(parents=True, exist_ok=True)
        client = binary.parent / 'linux64/steamclient.so'
        if not client.is_file():
            client = self.root / 'tools/steamcmd/linux64/steamclient.so'
        if client.is_file():
            shutil.copyfile(client, sdk / 'steamclient.so')
        else:
            raise ValueError('Steam client library is missing; run valheimctl update before starting.')
        log = data / 'server.log'
        if log.exists():
            log.replace(data / 'server.previous.log')
        environment = dict(os.environ, HOME=str(runtime), XDG_CONFIG_HOME=str(runtime / '.config'),
                           SteamAppId='892970', LD_LIBRARY_PATH=str(binary.parent / 'linux64'))
        environment.pop('LD_PRELOAD', None)
        arguments = [str(binary), '-nographics', '-batchmode', '-name', self.label(name),
                     '-port', str(self.config['port']), '-world', profile['world'],
                     '-password', password, '-public', '1' if profile['public'] else '0',
                     '-savedir', str(data), '-logFile', str(log),
                     '-saveinterval', str(self.config['save_interval']), '-backups', '4']
        os.chdir(binary.parent)
        os.execve(str(binary), arguments, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/valheim'))
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'run', 'start', 'stop', 'restart', 'status', 'logs', 'update'):
        commands.add_parser(name)
    commands.add_parser('worlds', aliases=['list'])
    commands.add_parser('generate').add_argument('name', nargs='?', type=identifier)
    for name in ('use', 'delete', 'settings'):
        commands.add_parser(name).add_argument('name', type=identifier)
    commands.add_parser('backup').add_argument('name', nargs='?', type=identifier)
    export = commands.add_parser('export')
    export.add_argument('name', type=identifier)
    export.add_argument('destination', nargs='?', type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('backup', type=Path)
    restore.add_argument('name', type=identifier)
    commands.add_parser('password').add_argument('--change', action='store_true')
    args = parser.parse_args()
    authorize_operator()
    manager = Manager(args.root)
    if args.command == 'run':
        manager.run()
        return
    with manager.lock():
        if args.command == 'init':
            manager.initialize()
        elif args.command in ('start', 'status', 'logs', 'update', 'stop'):
            getattr(manager, args.command)()
        elif args.command == 'restart':
            manager.service('stop')
            manager.start()
        elif args.command in ('worlds', 'list'):
            manager.worlds()
        elif args.command == 'generate':
            manager.generate(args.name)
        elif args.command == 'use':
            manager.select(args.name)
        elif args.command == 'delete':
            manager.delete(args.name)
        elif args.command == 'settings':
            print(json.dumps(manager.profile(args.name), indent=2))
        elif args.command == 'backup':
            manager.backup(args.name or manager.config['active'])
        elif args.command == 'export':
            manager.export(args.name, args.destination)
        elif args.command == 'restore':
            manager.restore(args.backup, args.name)
        elif args.command == 'password':
            manager.password(args.change)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
