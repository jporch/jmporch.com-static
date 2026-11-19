"""Reject unsafe or incomplete releases before opening an SSH connection."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy-release.sh'


class TestReleaseValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='jmporch-deploy-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'public'
        self.source.mkdir()
        (self.source / 'index.html').write_text('homepage')
        (self.source / 'feed.xml').write_text('<rss/>')
        (self.source / 'deployment.json').write_text(json.dumps({'release': 'test-123'}))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.connection = self.root / 'ssh-was-called'
        ssh = self.bin / 'ssh'
        ssh.write_text('#!/bin/sh\ntouch "$SSH_CALLED"\nexit 99\n')
        ssh.chmod(0o755)
        self.env = dict(os.environ, SSH_HOST='23.92.22.231', SSH_PORT='22',
                        SSH_USER='jmporch-deploy', SSH_KNOWN_HOSTS='unused',
                        SSH_PRIVATE_KEY='unused', DEPLOY_RELEASE='test-123',
                        SSH_CALLED=str(self.connection),
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'])

    def rejected(self, message):
        result = subprocess.run(['bash', str(SCRIPT), str(self.source)],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.connection.exists(), 'Invalid input must never reach SSH.')

    def test_release_id_cannot_escape_its_directory(self):
        self.env['DEPLOY_RELEASE'] = '../current'
        self.rejected('Invalid SSH settings or release ID')

    def test_shell_metacharacters_in_hostname_are_rejected(self):
        self.env['SSH_HOST'] = 'example.com;false'
        self.rejected('Invalid SSH settings or release ID')

    def test_missing_feed_does_not_upload(self):
        (self.source / 'feed.xml').unlink()
        self.rejected('missing feed.xml')

    def test_mismatched_artifact_does_not_upload(self):
        (self.source / 'deployment.json').write_text('{"release":"old-release"}')
        self.rejected('does not match the requested release')

    def test_symlink_payload_does_not_upload(self):
        (self.source / 'linked.html').symlink_to(self.source / 'index.html')
        self.rejected('symbolic links')


if __name__ == '__main__':
    unittest.main()
