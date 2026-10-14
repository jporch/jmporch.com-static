"""Integration checks using Python's standard library; no package installation."""
import argparse
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET


class Document(HTMLParser):
    def __init__(self, path):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.links = []
        self.controls = []
        self.collapse_count = 0
        self.feed(path.read_text())

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'collapse-toggle' in attrs.get('class', '').split():
            self.collapse_count += 1
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'aria-controls' in attrs:
            self.controls.extend(attrs['aria-controls'].split())
        for key in ('href', 'src'):
            if key in attrs:
                self.links.append(attrs[key])


def check_output(output, base_url):
    base_url = base_url.rstrip('/') + '/'
    base = urlsplit(base_url)
    documents = {path.resolve(): Document(path) for path in output.rglob('*.html')}
    for path, document in documents.items():
        duplicates = [key for key, count in Counter(document.ids).items() if count > 1]
        assert not duplicates, f'Duplicate IDs in {path}: {duplicates}'
        assert set(document.controls) <= set(document.ids), f'Broken control targets in {path}'
        page_url = urljoin(base_url, path.relative_to(output.resolve()).as_posix())
        for link in document.links:
            target = urlsplit(urljoin(page_url, link))
            if target.scheme not in ('http', 'https') or target.netloc != base.netloc:
                continue
            assert target.path == base.path.rstrip('/') or target.path.startswith(base.path), f'Link escapes deployment base: {link}'
            relative = unquote(target.path[len(base.path):])
            destination = output / relative
            if destination.is_dir():
                destination /= 'index.html'
            assert destination.is_file(), f'Missing local asset or page: {link} in {path}'
            if target.fragment and destination.suffix == '.html':
                assert unquote(target.fragment) in documents[destination.resolve()].ids, (
                    f'Missing anchor: {link} in {path}')
    home = documents[(output / 'index.html').resolve()]
    assert {'about', 'work', 'education', 'portfolio'} <= set(home.ids)
    for feed in output.rglob('*.xml'):
        ET.parse(feed)
    for stylesheet in output.rglob('*.css'):
        for value in re.findall(r'url\([\'\"]?([^\)\'\"]+)', stylesheet.read_text()):
            if not value.startswith(('data:', 'http:', 'https:')):
                assert (stylesheet.parent / value).is_file(), f'Missing CSS asset: {value}'


def check_portfolio_feed(root, base_url):
    data = tomllib.loads((root / 'data/portfolio.toml').read_text())
    expected = [item['url'] for group in data['groups'] for item in group['items'] if 'url' in item]
    feed = ET.parse(root / 'public/feed.xml')
    entries = feed.findall('channel/item')
    assert Counter(item.findtext('link') for item in entries) == Counter(expected), (
        'RSS must contain every linked portfolio item exactly once, excluding page notes.')
    identities = [item.findtext('guid') for item in entries]
    assert len(set(identities)) == len(identities), 'RSS identifiers must be unique.'
    assert feed.findtext('channel/link') == base_url.rstrip('/') + '/#portfolio'
    assert feed.find('channel/{http://www.w3.org/2005/Atom}link').get('href') == base_url.rstrip('/') + '/feed.xml'
    print(f'PASS: Portfolio RSS includes all {len(expected)} links with unique identities and correct deployment URLs.')


def check_blog(root, zola, base_url):
    # Temporary posts exercise page discovery, date sorting, pagination, feeds,
    # navigation, and draft exclusion without publishing demonstration content.
    with tempfile.TemporaryDirectory(prefix='jmporch-blog-check-') as directory:
        project = Path(directory)
        for folder in ('content', 'data', 'templates', 'static'):
            shutil.copytree(root / folder, project / folder)
        shutil.copy(root / 'zola.toml', project / 'zola.toml')
        for number in range(1, 13):
            (project / f'content/blog/integration-{number:02}.md').write_text(
                f'+++\ntitle = "Integration {number:02}"\ndate = 2024-01-{number:02}\n+++\n\n'
                f'Post **{number}** from a Markdown file.\n')
        (project / 'content/blog/integration-draft.md').write_text(
            '+++\ntitle = "Unpublished integration draft"\ndate = 2024-02-01\ndraft = true\n+++\nSecret draft.\n')
        subprocess.run([str(zola), '--root', str(project), 'build', '--base-url', base_url], check=True)
        output = project / 'public'
        check_output(output, base_url)
        listing = (output / 'blog/index.html').read_text()
        second = (output / 'blog/page/2/index.html').read_text()
        assert listing.index('Integration 12') < listing.index('Integration 11')
        assert 'Integration 01' not in listing and 'Integration 01' in second
        assert not (output / 'blog/integration-draft').exists()
        middle = (output / 'blog/integration-06/index.html').read_text()
        assert 'Older: Integration 05' in middle
        assert 'Newer: Integration 07' in middle
        atom = ET.parse(output / 'blog/atom.xml')
        entries = atom.findall('{http://www.w3.org/2005/Atom}entry')
        assert len(entries) == 12, f'Expected 12 feed entries, got {len(entries)}'
        assert 'Unpublished integration draft' not in (output / 'blog/atom.xml').read_text()
        assert 'id="blog_button"' in (output / 'index.html').read_text()
        print('PASS: Markdown posts, date order, pagination, older/newer links, feed, drafts, and sidebar discovery.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--zola', default='zola')
    parser.add_argument('--base-url', default='https://jporch.github.io/jmporch.com-static')
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    resolved = shutil.which(arguments.zola) or str(Path(arguments.zola).resolve())
    check_output(root / 'public', arguments.base_url)
    check_portfolio_feed(root, arguments.base_url)
    expected_entries = sum(len(list((root / 'content' / folder).glob('*.md'))) - 1
                           for folder in ('work', 'education'))
    expected_groups = (root / 'data/portfolio.toml').read_text().count('[[groups]]')
    assert Document(root / 'public/index.html').collapse_count == expected_entries + expected_groups, (
        'Homepage is missing a work, education, or portfolio entry; check the extra.entries lists.')
    print('PASS: Generated HTML, unique IDs, control targets, local links, subpath assets, and XML feeds.')
    check_blog(root, Path(resolved), arguments.base_url)
