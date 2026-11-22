"""Exercise saved-world safety with real archives and simulated service control."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/minecraft.py'
SPEC = importlib.util.spec_from_file_location('minecraft_manager', SCRIPT)
mc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mc)


class ManagerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='minecraft-manager-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = mc.Manager(self.root)
        mc.write_json(self.manager.config_path, {'active': None, 'java': '/fake/java'})
        self.jar = self.root / 'fixture.jar'
        self.make_jar('original')
        self.release = self.manager.store_jar(self.jar, '26.3')
        self.events = []
        self.running = False
        self.addCleanup(patch.stopall)
        patch.object(self.manager, 'download', return_value=self.release).start()
        patch.object(self.manager, 'own_data', side_effect=lambda path: None).start()
        patch.object(self.manager, 'active', side_effect=lambda: self.running).start()
        patch.object(self.manager, 'stopped', side_effect=self.require_stopped).start()
        patch.object(self.manager, 'service', side_effect=self.service).start()
        patch.object(self.manager, 'wait_ready', side_effect=lambda: self.events.append('ready')).start()
        patch.object(mc, 'java_major', return_value=25).start()
        patch('sys.stdout', new=io.StringIO()).start()
        self.manager.create('friends', '26.3')
        profile = self.manager.profile('friends')
        profile['eula'] = True
        mc.write_json(self.manager.profile_path('friends') / 'profile.json', profile)
        (self.manager.profile_path('friends') / 'data/world').mkdir()
        (self.manager.profile_path('friends') / 'data/world/level.dat').write_bytes(b'original world')

    def make_jar(self, contents):
        with zipfile.ZipFile(self.jar, 'w') as jar:
            jar.writestr('META-INF/MANIFEST.MF', 'Main-Class: net.minecraft.bundler.Main\n')
            jar.writestr('fixture', contents)

    def service(self, action):
        self.events.append(action)
        if action == 'stop':
            self.running = False
        elif action == 'start':
            self.running = True

    def require_stopped(self):
        if self.running:
            raise ValueError('Cannot archive a live world.')

    def backups(self, name='friends'):
        return list((self.root / 'backups' / name).glob('*.tar.gz'))

    def test_defaults_and_existing_profile_protection(self):
        profile = self.manager.profile('friends')
        self.assertEqual((profile['memory'], profile['max_players']), ('2G', 8))
        properties = (self.manager.profile_path('friends') / 'data/server.properties').read_text()
        self.assertIn('white-list=true', properties)
        self.assertIn('online-mode=true', properties)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.manager.create('friends', '26.3')

    def test_switch_preserves_separate_worlds_and_selected_builds(self):
        self.manager.create('family', '26.3')
        profile = self.manager.profile('family')
        profile['eula'] = True
        mc.write_json(self.manager.profile_path('family') / 'profile.json', profile)
        self.running = True
        self.manager.use('family')
        self.assertEqual(self.manager.config['active'], 'family')
        self.assertEqual(self.events, ['stop', 'start', 'ready'])
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.manager.profile_path('friends') / 'data/world/level.dat').read_bytes(), b'original world')
        self.assertFalse((self.manager.profile_path('family') / 'data/world').exists())

    def test_switch_to_unaccepted_eula_does_not_stop_running_server(self):
        self.manager.create('family', '26.3')
        self.running = True
        with self.assertRaisesRegex(ValueError, 'EULA'):
            self.manager.use('family')
        self.assertEqual(self.events, [])
        self.assertEqual(self.manager.config['active'], 'friends')

    def test_generate_preserves_access_settings_and_packs_but_resets_progress(self):
        source = self.manager.profile_path('friends') / 'data'
        properties = source / 'server.properties'
        properties.write_text(properties.read_text().replace('difficulty=normal', 'difficulty=hard')
                              + 'level-seed=111\n')
        (source / 'whitelist.json').write_text('[{"name":"AllowedPlayer","uuid":"player-id"}]')
        (source / 'ops.json').write_text('[{"name":"AdminPlayer","level":4}]')
        (source / 'world/players').mkdir()
        (source / 'world/players/inventory.dat').write_bytes(b'old inventory')
        (source / 'world/datapacks').mkdir()
        (source / 'world/datapacks/example.zip').write_bytes(b'world generation pack')
        self.manager.download.reset_mock()
        self.running = True
        self.manager.generate('new-start', -12345)
        fresh = self.manager.profile_path('new-start') / 'data'
        profile = self.manager.profile('new-start')
        self.assertEqual(self.events, ['stop', 'start', 'ready'])
        self.assertEqual(self.manager.config['active'], 'new-start')
        self.assertEqual((profile['release'], profile['memory'], profile['max_players'], profile['eula']),
                         (self.release, '2G', 8, True))
        self.assertEqual((profile['seed'], profile['source_profile']), (-12345, 'friends'))
        self.assertIn('level-seed=-12345\n', (fresh / 'server.properties').read_text())
        self.assertIn('difficulty=hard', (fresh / 'server.properties').read_text())
        self.assertEqual((fresh / 'whitelist.json').read_bytes(), (source / 'whitelist.json').read_bytes())
        self.assertEqual((fresh / 'ops.json').read_bytes(), (source / 'ops.json').read_bytes())
        self.assertEqual((fresh / 'world/datapacks/example.zip').read_bytes(), b'world generation pack')
        self.assertFalse((fresh / 'world/level.dat').exists())
        self.assertFalse((fresh / 'world/players').exists())
        self.assertEqual((source / 'world/level.dat').read_bytes(), b'original world')
        self.assertIn('level-seed=111\n', properties.read_text())
        self.assertEqual(len(self.backups()), 1)
        self.manager.download.assert_not_called()

    def test_generate_auto_name_and_random_seed_without_starting_stopped_game(self):
        name = self.manager.generate()
        mc.identifier(name)
        self.assertNotEqual(name, 'friends')
        profile = self.manager.profile(name)
        self.assertIsInstance(profile['seed'], int)
        self.assertTrue(-(2 ** 63) <= profile['seed'] < 2 ** 63)
        properties = (self.manager.profile_path(name) / 'data/server.properties').read_text()
        self.assertIn(f"level-seed={profile['seed']}\n", properties)
        self.assertEqual(self.manager.config['active'], name)
        self.assertEqual(self.events, [])

    def test_generate_invalid_requests_do_not_interrupt_or_change_current_world(self):
        self.running = True
        for name, seed in [('friends', None), ('../unsafe', None), ('new', 2 ** 63), ('new', -(2 ** 63) - 1)]:
            with self.assertRaises(ValueError):
                self.manager.generate(name, seed)
        self.assertEqual(self.events, [])
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertFalse(self.manager.profile_path('new').exists())
        self.assertEqual(len(self.backups()), 0)

    def test_generate_backup_failure_keeps_original_selected_and_running(self):
        self.running = True
        with patch.object(self.manager, 'archive', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.manager.generate('new')
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertFalse(self.manager.profile_path('new').exists())
        self.assertTrue(self.running)
        self.assertEqual(self.events, ['stop', 'start', 'ready'])

    def test_generate_preparation_failure_cleans_staging_and_resumes_original(self):
        self.running = True
        with patch.object(self.manager, 'own_data', side_effect=OSError('cannot prepare data')):
            with self.assertRaisesRegex(OSError, 'cannot prepare data'):
                self.manager.generate('new')
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertFalse(self.manager.profile_path('new').exists())
        self.assertEqual(list(self.manager.work_path.glob('.generate-*')), [])
        self.assertTrue(self.running)
        self.assertEqual(self.events, ['stop', 'start', 'ready'])

    def test_generate_failed_new_start_retains_both_worlds(self):
        self.running = True
        with patch.object(self.manager, 'wait_ready', side_effect=ValueError('new world failed')):
            with self.assertRaisesRegex(ValueError, 'new world failed'):
                self.manager.generate('new')
        self.assertEqual(self.manager.config['active'], 'new')
        self.assertTrue(self.manager.profile_path('new').is_dir())
        self.assertEqual((self.manager.profile_path('friends') / 'data/world/level.dat').read_bytes(), b'original world')
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.events, ['stop', 'start'])

    def test_generate_rejects_custom_engine_and_unsafe_world_folder_before_stopping(self):
        properties = self.manager.profile_path('friends') / 'data/server.properties'
        properties.write_text('level-name=../escape\n')
        self.running = True
        with self.assertRaises(ValueError):
            self.manager.generate('new')
        properties.write_text('level-name=world\n')
        profile = self.manager.profile('friends')
        self.make_jar('custom engine')
        profile['release'] = self.manager.store_jar(self.jar, 'custom', kind='custom-jar')
        mc.write_json(self.manager.profile_path('friends') / 'profile.json', profile)
        with self.assertRaisesRegex(ValueError, 'vanilla profiles only'):
            self.manager.generate('new')
        self.assertEqual(self.events, [])

    def test_worlds_marks_active_world_and_lists_inactive_world(self):
        self.manager.create('family', '26.3')
        mc.sys.stdout.seek(0)
        mc.sys.stdout.truncate(0)
        self.manager.worlds()
        output = mc.sys.stdout.getvalue()
        self.assertIn('* friends (active): 26.3, 8 players, 2G heap', output)
        self.assertIn('  family (inactive): 26.3, 8 players, 2G heap', output)

    def test_delete_inactive_world_keeps_backup_and_does_not_interrupt_active_world(self):
        self.manager.generate('new', 12345)
        previous_backups = set(self.backups())
        (self.manager.profile_path('friends') / 'data/world/level.dat').write_bytes(b'inactive world at deletion')
        self.running = True
        self.events.clear()
        self.manager.delete('friends')
        self.assertFalse(self.manager.profile_path('friends').exists())
        self.assertEqual(self.manager.config['active'], 'new')
        self.assertTrue(self.running)
        self.assertEqual(self.events, [])
        new_backups = set(self.backups()) - previous_backups
        self.assertEqual(len(new_backups), 1)
        backup = new_backups.pop()
        self.manager.restore(backup, 'restored-friends')
        self.assertEqual((self.manager.profile_path('restored-friends') / 'data/world/level.dat').read_bytes(), b'inactive world at deletion')
        self.assertEqual(self.manager.profile('restored-friends')['release'], self.release)

    def test_cli_generate_worlds_list_alias_and_delete(self):
        with patch.object(mc, 'Manager', return_value=self.manager), patch.object(mc, 'authorize_operator'):
            for arguments in (['generate', 'cli-world', '--seed', '-42'], ['worlds'],
                              ['list'], ['delete', 'friends']):
                with patch.object(mc.sys, 'argv', ['minecraftctl', *arguments]):
                    mc.main()
        self.assertEqual(self.manager.config['active'], 'cli-world')
        self.assertEqual(self.manager.profile('cli-world')['seed'], -42)
        self.assertFalse(self.manager.profile_path('friends').exists())

    def test_minecraft_login_is_authorized_without_root(self):
        account = SimpleNamespace(pw_name='minecraft', pw_uid=1234, pw_gid=1234)
        with patch.object(mc.pwd, 'getpwnam', return_value=account), \
             patch.object(mc.os, 'geteuid', return_value=1234), \
             patch.object(mc.os, 'setuid') as change_uid:
            mc.authorize_operator()
        change_uid.assert_not_called()

    def test_root_caller_drops_groups_gid_and_uid_before_any_manager_read(self):
        account = SimpleNamespace(pw_name='minecraft', pw_uid=1234, pw_gid=5678)
        changes = []
        with patch.object(mc.pwd, 'getpwnam', return_value=account), \
             patch.object(mc.os, 'geteuid', side_effect=[0, 1234]), \
             patch.object(mc.os, 'initgroups', side_effect=lambda name, gid: changes.append(('groups', name, gid))), \
             patch.object(mc.os, 'setgid', side_effect=lambda gid: changes.append(('gid', gid))), \
             patch.object(mc.os, 'setuid', side_effect=lambda uid: changes.append(('uid', uid))):
            mc.authorize_operator()
        self.assertEqual(changes, [('groups', 'minecraft', 5678), ('gid', 5678), ('uid', 1234)])
        order = []
        with patch.object(mc, 'authorize_operator', side_effect=lambda: order.append('authorize')), \
             patch.object(mc, 'Manager', side_effect=lambda root: order.append('manager') or self.manager), \
             patch.object(mc.sys, 'argv', ['minecraftctl', 'worlds']):
            mc.main()
        self.assertEqual(order, ['authorize', 'manager'])

    def test_unrelated_account_is_denied_before_manager_access(self):
        account = SimpleNamespace(pw_name='minecraft', pw_uid=1234, pw_gid=1234)
        with patch.object(mc.pwd, 'getpwnam', return_value=account), \
             patch.object(mc.os, 'geteuid', return_value=4321), \
             patch.object(mc, 'Manager') as manager, \
             patch.object(mc.sys, 'argv', ['minecraftctl', 'worlds']):
            with self.assertRaisesRegex(ValueError, 'Log in as minecraft'):
                mc.main()
            manager.assert_not_called()

    def test_service_helper_permits_only_fixed_minecraft_actions(self):
        for action in ('start', 'stop', 'restart'):
            self.assertEqual(mc.service_control(action),
                             ['/usr/bin/sudo', '-n', '--', '/usr/bin/systemctl', action, 'minecraft.service'])
        for action in ('enable', 'edit', 'start ssh.service', 'daemon-reload'):
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                mc.service_control(action)

    def test_data_ownership_is_checked_without_chown_and_symlinks_are_rejected(self):
        data = self.manager.profile_path('friends') / 'data'
        owner = data.stat().st_uid
        with patch.object(mc.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=owner)), \
             patch.object(mc.os, 'chown') as chown:
            mc.Manager.own_data(self.manager, data)
            chown.assert_not_called()
            (data / 'external-link').symlink_to(self.root / 'fixture.jar')
            with self.assertRaisesRegex(ValueError, 'Symlinks'):
                mc.Manager.own_data(self.manager, data)
        (data / 'external-link').unlink()
        with patch.object(mc.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=owner + 1)):
            with self.assertRaisesRegex(ValueError, 'owned by minecraft'):
                mc.Manager.own_data(self.manager, data)

    def test_service_and_logs_elevate_only_exact_commands_status_does_not(self):
        with patch.object(mc.subprocess, 'run') as run:
            mc.Manager.service(self.manager, 'start')
            run.assert_called_once_with(mc.service_control('start'), check=True)
        with patch.object(mc.subprocess, 'run') as run:
            mc.Manager.service(self.manager, 'status')
            run.assert_called_once_with(['/usr/bin/systemctl', '--no-pager', 'status', 'minecraft.service'], check=True)
        with patch.object(mc.subprocess, 'run') as run:
            self.manager.logs()
            run.assert_called_once_with(['/usr/bin/sudo', '-n', '--', '/usr/bin/journalctl',
                                         '-u', 'minecraft.service', '-n', '100', '--no-pager'], check=True)

    def test_world_data_ownership_check_needs_no_chown(self):
        data = self.manager.profile_path('friends') / 'data'
        account = SimpleNamespace(pw_uid=data.stat().st_uid)
        with patch.object(mc.pwd, 'getpwnam', return_value=account), patch.object(mc.os, 'chown') as chown:
            mc.Manager.own_data(self.manager, data)
        chown.assert_not_called()
        with patch.object(mc.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=-1)):
            with self.assertRaisesRegex(ValueError, 'owned by minecraft'):
                mc.Manager.own_data(self.manager, data)

    def test_delete_refuses_active_world_even_when_stopped_and_invalid_names(self):
        for running in (False, True):
            self.running = running
            with self.assertRaisesRegex(ValueError, 'active world'):
                self.manager.delete('friends')
        with self.assertRaises(ValueError):
            self.manager.delete('../escape')
        self.assertTrue(self.manager.profile_path('friends').is_dir())
        self.assertEqual(self.events, [])

    def test_delete_failed_backup_preserves_inactive_world(self):
        self.manager.create('family', '26.3')
        self.running = True
        with patch.object(self.manager, 'archive', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.manager.delete('family')
        self.assertTrue(self.manager.profile_path('family').is_dir())
        self.assertEqual(self.manager.config['active'], 'friends')
        self.assertEqual(self.events, [])

    def test_update_backup_contains_original_build_and_world(self):
        self.running = True
        self.make_jar('updated')
        new_release = self.manager.store_jar(self.jar, 'future')
        self.manager.update('friends', new_release)
        self.assertEqual(self.manager.profile('friends')['release'], new_release)
        with tarfile.open(self.backups()[0]) as archive:
            self.assertEqual(archive.extractfile('profile/data/world/level.dat').read(), b'original world')
            metadata = json.load(archive.extractfile('release.json'))
            self.assertEqual(metadata['id'], self.release)
        self.assertEqual(self.events, ['stop', 'start', 'ready'])

    def test_failed_backup_never_activates_update_and_restarts_old_server(self):
        self.running = True
        self.make_jar('updated')
        updated = self.manager.store_jar(self.jar, 'future')
        with patch.object(self.manager, 'archive', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.manager.update('friends', updated)
        self.assertEqual(self.manager.profile('friends')['release'], self.release)
        self.assertEqual(self.events, ['stop', 'start', 'ready'])

    def test_custom_engine_cannot_be_accidentally_replaced_by_vanilla(self):
        self.make_jar('custom engine')
        custom = self.manager.store_jar(self.jar, 'custom-26.3', kind='custom-jar')
        self.manager.update('friends', custom)
        self.running = True
        with self.assertRaisesRegex(ValueError, 'custom engine with vanilla'):
            self.manager.update('friends', self.release)
        self.assertEqual(self.manager.profile('friends')['release'], custom)
        self.assertEqual(self.events, [])

    def test_readiness_requires_a_minecraft_status_response(self):
        self.running = True
        payload = b'{"version":{},"players":{}}'
        stream = io.BytesIO(bytes([len(payload) + 2, 0, len(payload)]) + payload)
        sent = []
        stream.settimeout = lambda timeout: None
        stream.sendall = sent.append
        stream.recv = stream.read
        with patch.object(mc.socket, 'create_connection', return_value=stream):
            mc.Manager.wait_ready(self.manager)
        self.assertTrue(sent[0].endswith(b'\x01\x00'))

    def test_failed_start_retains_new_build_and_recovery_backup(self):
        self.running = True
        self.make_jar('updated')
        updated = self.manager.store_jar(self.jar, 'future')
        def fail_start(action):
            self.events.append(action)
            if action == 'stop':
                self.running = False
            elif action == 'start':
                raise OSError('startup failed')
        with patch.object(self.manager, 'service', side_effect=fail_start):
            with self.assertRaisesRegex(OSError, 'startup failed'):
                self.manager.update('friends', updated)
        self.assertEqual(self.manager.profile('friends')['release'], updated)
        self.assertFalse(self.running)
        self.assertEqual(len(self.backups()), 1)

    def test_settings_are_independent_and_validate_before_downtime(self):
        self.manager.settings('friends', '1536M', 6)
        profile = self.manager.profile('friends')
        self.assertEqual((profile['memory'], profile['max_players']), ('1536M', 6))
        self.assertEqual(self.events, [])
        self.assertEqual(len(self.backups()), 1)
        for maximum, players in [('256M', None), (None, 0), ('2G;bad', 8)]:
            with self.assertRaises(ValueError):
                self.manager.settings('friends', maximum, players)
        self.assertEqual(len(self.backups()), 1)

    def test_restore_recovers_a_new_profile_without_overwriting_current_world(self):
        backup = self.manager.backup('friends')
        world = self.manager.profile_path('friends') / 'data/world/level.dat'
        world.write_bytes(b'new world data')
        self.manager.restore(backup, 'recovered')
        self.assertEqual(world.read_bytes(), b'new world data')
        self.assertEqual((self.manager.profile_path('recovered') / 'data/world/level.dat').read_bytes(), b'original world')
        self.assertEqual(self.manager.profile('recovered')['release'], self.release)
        self.assertEqual(self.manager.config['active'], 'friends')
        with self.assertRaisesRegex(ValueError, 'new profile name'):
            self.manager.restore(backup, 'friends')

    def test_restore_rejects_traversal_and_symlinks(self):
        for name, link in [('../outside', False), ('profile/data/escape', True)]:
            archive_path = self.root / 'unsafe.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                archive.addfile(member)
            with self.assertRaisesRegex(ValueError, 'Unsafe backup'):
                self.manager.restore(archive_path, 'bad')
            self.assertFalse(self.manager.profile_path('bad').exists())

    def test_start_rejects_tampered_jar_and_old_java(self):
        with patch.object(mc, 'java_major', return_value=21):
            with self.assertRaisesRegex(ValueError, 'requires Java 25'):
                self.manager.start()
        (self.root / 'releases' / self.release / 'server.jar').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.manager.start()
        self.assertEqual(self.events, [])

    def test_download_checksum_failure_leaves_active_profile_untouched(self):
        self.running = True
        metadata = {'versions': [{'id': '26.3', 'type': 'release', 'url': 'https://example.invalid/version'}]}
        details = {'downloads': {'server': {'url': 'https://example.invalid/server.jar', 'size': 4,
                                            'sha1': 'wrong'}}, 'javaVersion': {'majorVersion': 25}}
        # Call the class method to bypass this instance's fixture download mock.
        with patch.object(mc, 'fetch_json', side_effect=[metadata, details]), \
             patch.object(mc.urllib.request, 'urlopen', return_value=io.BytesIO(b'jar!')):
            with self.assertRaisesRegex(ValueError, 'checksum/size mismatch'):
                mc.Manager.download(self.manager, '26.3')
        self.assertEqual(self.events, [])
        self.assertEqual(self.manager.profile('friends')['release'], self.release)

    def test_failed_shutdown_cannot_be_treated_as_stopped_with_a_live_pid(self):
        # Call the real guard (instance method is replaced for other tests).
        with patch.object(mc.subprocess, 'check_output', return_value='ActiveState=failed\nMainPID=123\nControlPID=0\n'):
            with self.assertRaisesRegex(ValueError, 'not fully stopped'):
                mc.Manager.stopped(self.manager)

    def test_unsafe_names_are_rejected(self):
        for name in ('../world', '/tmp/world', 'world;stop', '', None):
            with self.assertRaises(ValueError):
                self.manager.profile_path(name)


if __name__ == '__main__':
    unittest.main()
