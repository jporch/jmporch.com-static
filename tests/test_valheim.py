"""World safety, Steam installation staging, exports and Valheim runtime locking."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


vh = module('valheim_manager', 'valheim.py')
REAL_UNIT_STATE = vh.unit_state


class Worlds(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='valheim-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('state', 'profiles', 'releases', 'tools', 'backups', 'exports'):
            (self.root / name).mkdir()
        self.manager = vh.Manager(self.root)
        self.running = False
        self.events = []
        self.addCleanup(patch.stopall)
        patch('sys.stdout', new=io.StringIO()).start()
        patch.object(vh, 'unit_state', side_effect=self.state).start()
        patch.object(self.manager, 'service', side_effect=self.service).start()
        patch.object(vh, 'query_info', side_effect=lambda *args: {
            'name': self.manager.label(self.manager.config['active']), 'players': 0,
            'max_players': 10, 'version': 'fixture'}).start()
        slot = self.root / 'game-slot'
        slot.touch()
        patch.object(vh, 'RUNTIME_LOCK', slot).start()
        self.manager.initialize()
        self.release = self.make_release('steam-12345-fixture', '12345')
        config = self.manager.config
        config['default_release'] = self.release
        vh.write_json(self.manager.config_path, config)
        self.manager.generate('friends')
        self.save_world('friends', b'original terrain')

    def make_release(self, name, build):
        path = self.root / 'releases' / name
        (path / 'linux64').mkdir(parents=True)
        binary = path / 'valheim_server.x86_64'
        binary.write_bytes(b'\x7fELF' + build.encode())
        binary.chmod(0o755)
        (path / 'linux64/steamclient.so').write_bytes(b'client fixture')
        vh.write_json(path / 'release.json', {'id': name, 'build_id': build,
            'app_id': vh.APP_ID, 'binary_sha256': vh.sha256(binary), 'installed_at': 'fixture'})
        return name

    def state(self, unit):
        self.assertEqual(unit, vh.SERVICE)
        active = self.running
        return {'ActiveState': 'active' if active else 'inactive',
                'MainPID': '123' if active else '0', 'ControlPID': '0'}

    def service(self, action):
        self.events.append(action)
        if action == 'stop':
            self.running = False
        elif action == 'start':
            self.running = True
            (self.manager.profile_path(self.manager.config['active']) / 'data/server.log').write_text('Game server connected\nOpened Steam server\n')

    def save_world(self, name, content):
        path = self.manager.profile_path(name) / 'data/worlds_local' / self.manager.profile(name)['world']
        path.mkdir(parents=True, exist_ok=True)
        (path / '_main.1.fwl2').write_bytes(b'world metadata')
        (path / 'terrain.chunk').write_bytes(content)
        return path

    def backups(self, name='friends'):
        return list((self.root / 'backups' / name).glob('*.tar.gz'))

    def test_unlisted_start_and_status_do_not_require_steam_browser_queries(self):
        with patch.object(vh, 'query_info', side_effect=TimeoutError('unlisted')) as query:
            self.manager.start()
            self.manager.status()
        self.assertTrue(self.running)
        query.assert_not_called()

    def test_public_start_requires_a_successful_steam_browser_query(self):
        profile = self.manager.profile('friends')
        profile['public'] = True
        vh.write_json(self.manager.profile_path('friends') / 'profile.json', profile)
        with patch.object(vh, 'query_info', return_value={'name': self.manager.label('friends')}) as query:
            self.manager.start()
        query.assert_called_once_with('127.0.0.1', 2457)

    def test_unlisted_start_waits_until_steam_server_has_opened(self):
        self.manager.service = lambda action: setattr(self, 'running', True)
        log = self.manager.profile_path('friends') / 'data/server.log'
        log.write_text('Game server connected\n')
        with patch.object(vh.time, 'monotonic', side_effect=[0, 1, 301]), patch.object(vh.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'Readiness not confirmed'):
                self.manager.start()

    def test_generate_switches_and_preserves_old_world_and_access_lists(self):
        data = self.manager.profile_path('friends') / 'data'
        (data / 'permittedlist.txt').write_text('Steam_123456\n')
        self.running = True
        self.manager.generate('fresh')
        self.assertEqual(self.manager.config['active'], 'fresh')
        self.assertEqual(self.events, ['stop', 'start'])
        self.assertEqual((self.manager.profile_path('fresh') / 'data/permittedlist.txt').read_text(), 'Steam_123456\n')
        self.assertFalse((self.manager.profile_path('fresh') / 'data/worlds_local').exists())
        self.assertEqual((data / 'worlds_local/friends/terrain.chunk').read_bytes(), b'original terrain')
        self.assertEqual(len(self.backups()), 1)

    def test_generate_auto_name_selects_without_starting_when_off(self):
        self.manager.generate()
        name = self.manager.config['active']
        vh.identifier(name)
        self.assertNotEqual(name, 'friends')
        self.assertEqual(self.events, [])

    def test_duplicate_and_unsafe_names_never_change_or_stop_the_game(self):
        self.running = True
        for name in ('friends', '../outside', 'bad name', '/tmp/world'):
            with self.assertRaises(ValueError):
                self.manager.generate(name)
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertEqual(self.events, [])

    def test_failed_switch_backup_resumes_original_and_keeps_both_profiles(self):
        self.running = True
        with patch.object(self.manager, 'archive', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.manager.generate('fresh')
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertTrue(self.running)
        self.assertTrue(self.manager.profile_path('fresh').is_dir())

    def test_delete_is_inactive_only_and_recovery_uses_matching_retained_build(self):
        self.manager.generate('fresh')
        self.running = True
        before = set(self.backups())
        self.manager.delete('friends')
        self.assertEqual(self.events, [])
        self.assertTrue(self.running)
        self.assertFalse(self.manager.profile_path('friends').exists())
        created = set(self.backups()) - before
        self.assertEqual(len(created), 1)
        self.manager.restore(created.pop(), 'recovered')
        self.assertEqual(self.manager.profile('recovered')['release'], self.release)
        self.assertEqual((self.manager.profile_path('recovered') / 'data/worlds_local/friends/terrain.chunk').read_bytes(), b'original terrain')
        for running in (False, True):
            self.running = running
            with self.assertRaisesRegex(ValueError, 'active world'):
                self.manager.delete('fresh')

    def test_failed_delete_backup_keeps_world(self):
        self.manager.generate('fresh')
        with patch.object(self.manager, 'archive', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.manager.delete('friends')
        self.assertTrue(self.manager.profile_path('friends').is_dir())

    def test_export_contains_complete_world_folder_without_password_or_access_policy(self):
        data = self.manager.profile_path('friends') / 'data'
        (data / 'permittedlist.txt').write_text('Steam_private\n')
        self.running = True
        result = self.manager.export('friends')
        self.assertEqual(self.events, ['stop', 'start'])
        self.assertTrue(self.running)
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(set(archive.namelist()), {'friends/_main.1.fwl2', 'friends/terrain.chunk'})
            self.assertEqual(archive.read('friends/terrain.chunk'), b'original terrain')
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, 'destination exists'):
            self.manager.export('friends', result)

    def test_export_inactive_world_does_not_stop_active_world(self):
        self.manager.generate('fresh')
        self.running = True
        self.manager.export('friends')
        self.assertEqual(self.events, [])

    def test_empty_world_directory_is_not_exported_as_a_valid_save(self):
        import shutil
        folder = self.manager.profile_path('friends') / 'data/worlds_local/friends'
        shutil.rmtree(folder)
        folder.mkdir()
        with self.assertRaisesRegex(ValueError, 'No saved world'):
            self.manager.export('friends')
        self.assertEqual(list((self.root / 'exports').iterdir()), [])

    def test_native_launcher_uses_steam_only_and_keeps_runtime_away_from_ssh_home(self):
        with patch.object(vh.os, 'chdir'), patch.object(vh.os, 'execve') as execute:
            self.manager.run()
        binary, arguments, environment = execute.call_args.args
        self.assertEqual(arguments[0], binary)
        self.assertNotIn('-crossplay', arguments)
        self.assertEqual(arguments[arguments.index('-public') + 1], '0')
        self.assertEqual(arguments[arguments.index('-world') + 1], 'friends')
        data = self.manager.profile_path('friends') / 'data'
        self.assertEqual(arguments[arguments.index('-savedir') + 1], str(data))
        self.assertEqual(environment['HOME'], str(data / '.runtime'))
        self.assertEqual(environment['SteamAppId'], '892970')
        self.assertTrue((data / '.runtime/.steam/sdk64/steamclient.so').is_file())

    def test_legacy_export_requires_both_files_and_never_includes_other_worlds(self):
        saved = self.manager.profile_path('friends') / 'data/worlds_local'
        import shutil
        shutil.rmtree(saved / 'friends')
        (saved / 'friends.db').write_bytes(b'legacy data')
        with self.assertRaisesRegex(ValueError, 'No saved world'):
            self.manager.export('friends')
        (saved / 'friends.fwl').write_bytes(b'legacy metadata')
        (saved / 'another.db').write_bytes(b'other world')
        result = self.manager.export('friends')
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(set(archive.namelist()), {'friends.db', 'friends.fwl'})

    def test_export_rejects_symlinks_and_resumes_game_after_failure(self):
        folder = self.manager.profile_path('friends') / 'data/worlds_local/friends'
        (folder / 'outside').symlink_to(self.root / 'state/password')
        self.running = True
        with self.assertRaisesRegex(ValueError, 'Unsupported file'):
            self.manager.export('friends')
        self.assertTrue(self.running)
        self.assertEqual(list((self.root / 'exports').iterdir()), [])

    def test_backup_excludes_runtime_cache_and_logs_but_keeps_world(self):
        data = self.manager.profile_path('friends') / 'data'
        (data / '.runtime').mkdir()
        (data / '.runtime/outside').symlink_to(self.root / 'state/password')
        (data / 'server.log').write_text('log data')
        result = self.manager.backup('friends')
        with tarfile.open(result) as archive:
            names = archive.getnames()
            self.assertIn('profile/data/worlds_local/friends/terrain.chunk', names)
            self.assertIn('release.json', names)
            self.assertFalse(any('.runtime' in name or name.endswith('server.log') for name in names))

    def test_restore_rejects_traversal_and_symlinks(self):
        for filename, is_link in [('../outside', False), ('profile/data/link', True)]:
            path = self.root / 'unsafe.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                member = tarfile.TarInfo(filename)
                if is_link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                archive.addfile(member)
            with self.assertRaisesRegex(ValueError, 'Unsafe backup'):
                self.manager.restore(path, 'unsafe')
            self.assertFalse(self.manager.profile_path('unsafe').exists())

    def test_update_failure_keeps_old_build_and_resumes_without_downgrade(self):
        self.running = True
        with patch.object(self.manager, 'download_build', side_effect=OSError('download failed')):
            with self.assertRaisesRegex(OSError, 'download failed'):
                self.manager.update()
        self.assertEqual(self.manager.profile('friends')['release'], self.release)
        self.assertTrue(self.running)
        self.assertEqual(self.events, ['stop', 'start'])

    def test_update_retains_old_build_and_matching_backup(self):
        newer = self.make_release('steam-54321-fixture', '54321')
        self.running = True
        with patch.object(self.manager, 'download_build', return_value=newer):
            self.manager.update()
        self.assertEqual(self.manager.profile('friends')['release'], newer)
        self.assertTrue((self.root / 'releases' / self.release).is_dir())
        with tarfile.open(self.backups()[0]) as archive:
            self.assertEqual(json.load(archive.extractfile('release.json'))['id'], self.release)

    def test_update_does_not_resume_after_selected_build_fails_start(self):
        newer = self.make_release('steam-54321-fixture', '54321')
        self.running = True
        with patch.object(self.manager, 'download_build', return_value=newer), \
             patch.object(self.manager, 'start', side_effect=ValueError('startup failed')):
            with self.assertRaisesRegex(ValueError, 'startup failed'):
                self.manager.update()
        self.assertEqual(self.manager.profile('friends')['release'], newer)
        self.assertEqual(self.events, ['stop'])

    def test_download_build_validates_steam_manifest_and_stages_before_selection(self):
        tool = self.root / 'fake-steamcmd'
        tool.write_text('#!' + sys.executable + '\n' + '''import pathlib, sys
assert sys.argv[sys.argv.index('+login')+1] == 'anonymous'
assert sys.argv[sys.argv.index('+app_update')+1] == '896660'
root=pathlib.Path(sys.argv[sys.argv.index('+force_install_dir')+1])
(root/'steamapps').mkdir()
(root/'linux64').mkdir()
(root/'steamapps/appmanifest_896660.acf').write_text('"buildid" "98765"')
(root/'valheim_server.x86_64').write_bytes(b'\\x7fELFfixture')
''')
        tool.chmod(0o755)
        with patch.object(self.manager, 'install_steamcmd', return_value=tool):
            release = self.manager.download_build()
        self.assertEqual(self.manager.release(release)['build_id'], '98765')
        self.assertEqual(self.manager.profile('friends')['release'], self.release)
        self.assertEqual(self.manager.config['active'], 'friends')

    def test_start_checks_binary_integrity_before_service_action(self):
        (self.root / 'releases' / self.release / 'valheim_server.x86_64').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.manager.start()
        self.assertEqual(self.events, [])

    def test_secret_stays_private_and_initialization_preserves_existing_state(self):
        password = (self.root / 'state/password').read_text()
        self.manager.initialize()
        self.assertEqual((self.root / 'state/password').read_text(), password)
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertEqual((self.root / 'state/password').stat().st_mode & 0o777, 0o600)

    def test_service_permissions_have_no_arbitrary_action_or_unit(self):
        with patch.object(vh.subprocess, 'run') as run:
            vh.Manager.service(self.manager, 'start')
            run.assert_called_once_with(['/usr/bin/sudo', '-n', '--', '/usr/bin/systemctl', 'start', 'valheim.service'], check=True)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            vh.Manager.service(self.manager, 'edit ssh.service')


class NetworkAndRuntime(unittest.TestCase):
    def test_only_valheim_account_is_authorized_without_root(self):
        account = SimpleNamespace(pw_name='valheim', pw_uid=1234, pw_gid=1234)
        with patch.object(vh.pwd, 'getpwnam', return_value=account), patch.object(vh.os, 'geteuid', return_value=1234):
            vh.authorize_operator()
        with patch.object(vh.pwd, 'getpwnam', return_value=account), patch.object(vh.os, 'geteuid', return_value=4321), \
             patch.object(vh, 'Manager') as manager, patch.object(vh.sys, 'argv', ['valheimctl', 'worlds']):
            with self.assertRaisesRegex(ValueError, 'Log in as valheim'):
                vh.main()
            manager.assert_not_called()

    def test_valheim_runtime_lock_cannot_be_acquired_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'slot'
            path.touch()
            with vh.reserve_valheim_runtime(path):
                with self.assertRaisesRegex(ValueError, 'runtime lock'):
                    with vh.reserve_valheim_runtime(path):
                        self.fail('A second Valheim action acquired the same lock.')
            with vh.reserve_valheim_runtime(path):
                pass

    def test_udp_query_handles_steam_challenge_and_parses_server_info(self):
        packet = (b'\xff\xff\xff\xffI\x11' + b'My server\x00friends\x00valheim\x00Valheim\x00'
                  + b'\x00\x00' + bytes([2, 10, 0]) + b'dl\x01\x00' + b'1.0\x00')
        stream = unittest.mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.recv.side_effect = [b'\xff\xff\xff\xffA1234', packet]
        with patch.object(vh.socket, 'socket', return_value=stream):
            status = vh.query_info('127.0.0.1', 2457)
        self.assertEqual((status['name'], status['players'], status['max_players'], status['version']),
                         ('My server', 2, 10, '1.0'))
        self.assertTrue(stream.send.call_args_list[-1].args[0].endswith(b'1234'))


if __name__ == '__main__':
    unittest.main()
