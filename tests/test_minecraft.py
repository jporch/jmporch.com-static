"""Exercise saved-world safety with real archives and simulated service control."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
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
        mc.write_json(self.root / 'config.json', {'active': None, 'java': '/fake/java'})
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
