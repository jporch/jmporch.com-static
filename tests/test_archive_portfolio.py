"""Check incremental mirror preservation without making network requests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import archive_portfolio as mirror
except ModuleNotFoundError as error:
    if error.name not in ('bs4', 'markdownify'):
        raise
    raise unittest.SkipTest('Optional mirror dependencies are not installed')


class MirrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in [('ROOT', self.root), ('CACHE', self.root/'.cache'), ('CAPTURED', '2026-10-22')]:
            p = patch.object(mirror, name, value)
            p.start()
            self.addCleanup(p.stop)
        (self.root/'data').mkdir()
        (self.root/'static/mirror').mkdir(parents=True)

    def inventory(self, *items):
        text = '[[groups]]\ntitle = "Writing"\n'
        for item in items:
            text += '\n[[groups.items]]\n'
            text += '\n'.join(f'{k} = {json.dumps(v)}' for k, v in item.items()) + '\n'
        (self.root/'data/portfolio.toml').write_text(text)

    def item(self, ident, **extra):
        return {'id': ident, 'label': ident.title(), 'url': 'https://example.org/'+ident, **extra}

    def existing(self, ident):
        folder = self.root/'content/mirror'/ident
        folder.mkdir(parents=True)
        (folder/'index.md').write_text('Existing article; never replace me.')
        (folder/'image.png').write_bytes(b'original image bytes')
        return folder

    def audit(self):
        return json.loads((self.root/'static/mirror/audit.json').read_text())

    def test_existing_pages_skip_network_even_without_audit_or_after_url_change(self):
        folder = self.existing('existing')
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
        self.inventory(self.item('existing', url='https://different.example/article'))
        with patch.object(mirror, 'fetch', side_effect=AssertionError('Unexpected network')) as fetch:
            mirror.regenerate()
        fetch.assert_not_called()
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()})

    def test_only_missing_sources_are_fetched_and_unavailable_items_have_no_page(self):
        self.existing('old')
        old = {'id': 'old', 'status': 'mirrored', 'captured': '2001-02-03', 'custom': 'keep'}
        (self.root/'static/mirror/audit.json').write_text(json.dumps({'items': [old]}))
        self.inventory(self.item('old'), self.item('new'), self.item('missing'))
        def fetch(url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            body = '<title>New</title><div class="entry-content"><p>' + 'New article content. '*150 + '</p></div>'
            dest.write_text(body if url.endswith('/new') else 'Not found')
            return {'url': url, 'status': 200 if url.endswith('/new') else 404,
                    'final_url': url, 'path': str(dest), 'type': 'text/html', 'error': ''}
        with patch.object(mirror, 'fetch', side_effect=fetch) as mocked:
            mirror.regenerate()
        self.assertEqual({c.args[0] for c in mocked.call_args_list}, {'https://example.org/new', 'https://example.org/missing'})
        self.assertTrue((self.root/'content/mirror/new/index.md').is_file())
        self.assertFalse((self.root/'content/mirror/missing/index.md').exists())
        audit = self.audit()
        self.assertEqual(next(r for r in audit['items'] if r['id']=='old'), old)
        self.assertEqual(audit['generated'], '2026-10-22')

    def test_new_supplied_text_is_imported_without_network(self):
        folder = self.root/'content/mirror/local'
        folder.mkdir(parents=True)
        raw = b'https://example.org/local\r\n\r\nAn original paragraph.\r\n'
        (folder/'source.txt').write_bytes(raw)
        self.inventory(self.item('local', mirror_text='content/mirror/local/source.txt'))
        with patch.object(mirror, 'fetch', side_effect=AssertionError('Unexpected network')) as fetch:
            mirror.regenerate()
        fetch.assert_not_called()
        self.assertEqual((folder/'source.txt').read_bytes(), raw)
        self.assertIn('An original paragraph.', (folder/'index.md').read_text())


if __name__ == '__main__':
    unittest.main()
