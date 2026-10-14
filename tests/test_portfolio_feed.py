"""Exercise feed identity and ordering against real temporary Git histories."""
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from portfolio_feed import render_feed


class PortfolioFeedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='portfolio-history-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '-q', '-b', 'main')
        self.groups = [{'title': 'Writing', 'items': [
            {'id': 'legacy', 'label': 'Legacy article', 'url': 'https://example.com/legacy',
             'feed_title': 'Original feed title', 'guid': 'https://example.com/legacy-guid',
             'description': 'Original description', 'feed_date': '2000-01-01T12:00:00+00:00'},
            {'id': 'undated', 'label': 'Undated project', 'url': 'https://example.com/undated',
             'legacy_undated': True},
            {'note': 'A page note, not a feed item.'},
        ]}]
        self.save()
        self.commit('2020-01-01T00:00:00Z')

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.root), *arguments], check=True,
                              capture_output=True, text=True).stdout

    def save(self):
        source = self.root / 'data/portfolio.toml'
        source.parent.mkdir(exist_ok=True)
        lines = ['[feed]', 'title = "Portfolio"', 'description = "Recent additions"']
        for group in self.groups:
            lines.extend(['[[groups]]', f'title = {json.dumps(group["title"])}'])
            for item in group['items']:
                lines.append('[[groups.items]]')
                for key, value in item.items():
                    lines.append(f'{key} = {json.dumps(value)}')
        source.write_text('\n'.join(lines) + '\n')

    def commit(self, timestamp):
        self.git('add', '.')
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Feed Test',
                        '-c', 'user.email=feed-test@example.invalid', '-c', 'core.hooksPath=/dev/null',
                        'commit', '-q', '-m', 'Fixture change'], check=True, capture_output=True,
                       env={**os.environ, 'GIT_AUTHOR_DATE': timestamp, 'GIT_COMMITTER_DATE': timestamp})

    def entries(self):
        return ET.fromstring(render_feed(self.root, 'https://example.com/project')).findall('channel/item')

    def new_item(self, identity):
        return {'id': identity, 'label': identity, 'url': f'https://example.com/{identity}'}

    def test_legacy_metadata_and_unknown_dates_are_preserved(self):
        entries = self.entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].findtext('title'), 'Original feed title')
        self.assertEqual(entries[0].findtext('guid'), 'https://example.com/legacy-guid')
        self.assertEqual(entries[0].findtext('description'), 'Original description')
        self.assertEqual(parsedate_to_datetime(entries[0].findtext('pubDate')).year, 2000)
        self.assertIsNone(entries[1].find('pubDate'))

    def test_edits_moves_url_changes_and_unrelated_builds_keep_first_date(self):
        item = self.new_item('new-article')
        self.groups[0]['items'].append(item)
        self.save()
        self.commit('2021-02-03T12:34:56Z')
        initial = self.entries()[0]
        self.assertEqual(initial.findtext('guid'), 'urn:jmporch:portfolio:new-article')
        self.assertEqual(parsedate_to_datetime(initial.findtext('pubDate')).isoformat(),
                         '2021-02-03T12:34:56+00:00')
        self.groups[0]['items'].remove(item)
        item['url'] = 'https://example.com/new-location'
        item['label'] = 'Edited title'
        self.groups.append({'title': 'Projects', 'items': [item]})
        self.save()
        self.commit('2022-01-01T00:00:00Z')
        edited = self.entries()[0]
        self.assertEqual(edited.findtext('pubDate'), initial.findtext('pubDate'))
        self.assertEqual(edited.findtext('guid'), initial.findtext('guid'))
        before = render_feed(self.root, 'https://example.com/project')
        (self.root / 'unrelated.txt').write_text('An unrelated change')
        self.commit('2023-01-01T00:00:00Z')
        self.assertEqual(render_feed(self.root, 'https://example.com/project'), before)

    def test_same_commit_ties_follow_file_order_and_new_commits_sort_first(self):
        self.groups[0]['items'].extend([self.new_item('second'), self.new_item('first')])
        self.save()
        self.commit('2021-01-01T00:00:00Z')
        entries = self.entries()
        self.assertEqual([item.findtext('title') for item in entries[:2]], ['second', 'first'])
        self.assertEqual(entries[0].findtext('pubDate'), entries[1].findtext('pubDate'))
        self.groups[0]['items'].append(self.new_item('newest'))
        self.save()
        self.commit('2022-01-01T00:00:00Z')
        self.assertEqual(self.entries()[0].findtext('title'), 'newest')

    def test_uncommitted_items_preview_first_without_inventing_dates(self):
        self.groups[0]['items'].append(self.new_item('preview'))
        self.save()
        first = self.entries()[0]
        self.assertEqual(first.findtext('title'), 'preview')
        self.assertIsNone(first.find('pubDate'))

    def test_readding_an_identity_retains_its_original_date(self):
        item = self.new_item('returning')
        self.groups[0]['items'].append(item)
        self.save()
        self.commit('2021-01-01T00:00:00Z')
        original = self.entries()[0].findtext('pubDate')
        self.groups[0]['items'].remove(item)
        self.save()
        self.commit('2022-01-01T00:00:00Z')
        self.groups[0]['items'].append(item)
        self.save()
        self.commit('2023-01-01T00:00:00Z')
        self.assertEqual(self.entries()[0].findtext('pubDate'), original)

    def test_xml_escaping_plain_titles_and_deployment_urls(self):
        item = self.new_item('escaping')
        item.update(label='A <cite>Journal &amp; Review</cite>', description='Quotes " & <markup>')
        self.groups[0]['items'].append(item)
        self.save()
        root = ET.fromstring(render_feed(self.root, 'https://example.com/subpath/'))
        self.assertEqual(root.findtext('channel/item/title'), 'A Journal & Review')
        self.assertEqual(root.findtext('channel/item/description'), 'Quotes " & <markup>')
        self.assertEqual(root.findtext('channel/link'), 'https://example.com/subpath/#portfolio')
        self.assertEqual(root.find('channel/{http://www.w3.org/2005/Atom}link').get('href'),
                         'https://example.com/subpath/feed.xml')

    def test_duplicate_or_missing_ids_fail(self):
        self.groups[0]['items'].append(self.new_item('legacy'))
        self.save()
        with self.assertRaisesRegex(ValueError, 'unique, stable'):
            self.entries()
        del self.groups[0]['items'][-1]['id']
        self.save()
        with self.assertRaisesRegex(ValueError, 'unique, stable'):
            self.entries()

    def test_shallow_history_fails_instead_of_changing_dates(self):
        with tempfile.TemporaryDirectory(prefix='portfolio-shallow-') as directory:
            clone = Path(directory) / 'clone'
            self.git('clone', '-q', '--depth=1', self.root.as_uri(), str(clone))
            with self.assertRaisesRegex(ValueError, 'full Git history'):
                render_feed(clone, 'https://example.com')


if __name__ == '__main__':
    unittest.main()
