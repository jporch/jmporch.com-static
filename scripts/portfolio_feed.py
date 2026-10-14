"""Generate portfolio RSS from TOML and Git history (Python 3.11+, stdlib only)."""
from datetime import date, datetime, time, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

SOURCE = 'data/portfolio.toml'
ATOM = 'http://www.w3.org/2005/Atom'
ET.register_namespace('atom', ATOM)


def git(root, *arguments):
    result = subprocess.run(['git', '-C', str(root), *arguments],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def first_added_dates(root):
    if git(root, 'rev-parse', '--is-shallow-repository') == 'true':
        raise ValueError('Portfolio dates require full Git history. Fetch with --unshallow, '
                         'or configure actions/checkout with fetch-depth: 0.')
    dates = {}
    history = git(root, 'log', '--reverse', '--topo-order', '--format=%H%x09%ct', '--', SOURCE)
    for line in history.splitlines():
        commit, timestamp = line.split('\t')
        snapshot = subprocess.run(['git', '-C', str(root), 'show', f'{commit}:{SOURCE}'],
                                  capture_output=True, text=True)
        # A historical deletion or malformed intermediate edit is not an entry.
        if snapshot.returncode:
            continue
        try:
            data = tomllib.loads(snapshot.stdout)
        except tomllib.TOMLDecodeError:
            continue
        for group in data.get('groups', []):
            for item in group.get('items', []):
                if item.get('id') and item.get('url'):
                    dates.setdefault(item['id'], datetime.fromtimestamp(int(timestamp), timezone.utc))
    return dates


class PlainText(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(value)

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    return ''.join(PlainText(value).parts).strip()


def utc_date(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time(), timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('feed_date must be an ISO date or a timestamp with a timezone.')
    return value.astimezone(timezone.utc)


def render_feed(root, base_url):
    data = tomllib.loads((root / SOURCE).read_text())
    first_added = first_added_dates(root)
    items = []
    seen = set()
    for group in data['groups']:
        for item in group['items']:
            if 'url' not in item:
                continue
            identity = item.get('id', '')
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', identity) or identity in seen:
                raise ValueError(f'Portfolio item requires a unique, stable lowercase id: {identity!r}')
            seen.add(identity)
            if urlsplit(item['url']).scheme not in ('http', 'https'):
                raise ValueError(f'Portfolio URL must use HTTP or HTTPS: {item["url"]}')
            legacy_undated = item.get('legacy_undated', False)
            added = (utc_date(item['feed_date']) if 'feed_date' in item
                     else None if legacy_undated else first_added.get(identity))
            # New, uncommitted items preview first without inventing a timestamp.
            # Undated legacy items remain at the end in their current file order.
            rank = 2 if legacy_undated else 0 if added is None else 1
            items.append((rank, -(added.timestamp() if added else 0), len(items), item, added, group['title']))
    items.sort(key=lambda item: item[:3])
    base_url = base_url.rstrip('/') + '/'
    rss = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = data['feed']['title']
    ET.SubElement(channel, 'link').text = base_url + '#portfolio'
    ET.SubElement(channel, f'{{{ATOM}}}link', {
        'href': base_url + 'feed.xml', 'rel': 'self', 'type': 'application/rss+xml',
    })
    ET.SubElement(channel, 'description').text = data['feed']['description']
    dated = [entry[4] for entry in items if entry[4] is not None]
    if dated:
        ET.SubElement(channel, 'lastBuildDate').text = format_datetime(max(dated))
    for _, _, _, item, added, category in items:
        entry = ET.SubElement(channel, 'item')
        ET.SubElement(entry, 'title').text = plain_text(item.get('feed_title', item['label']))
        ET.SubElement(entry, 'link').text = item['url']
        guid = item.get('guid', f'urn:jmporch:portfolio:{item["id"]}')
        ET.SubElement(entry, 'guid', {
            'isPermaLink': str(urlsplit(guid).scheme in ('http', 'https')).lower(),
        }).text = guid
        if item.get('description'):
            ET.SubElement(entry, 'description').text = item['description']
        ET.SubElement(entry, 'category').text = category
        if added is not None:
            ET.SubElement(entry, 'pubDate').text = format_datetime(added)
    ET.indent(rss, space='  ')
    return ET.tostring(rss, encoding='utf-8', xml_declaration=True) + b'\n'


def prepare_feed(root, base_url):
    output = root / 'static/feed.xml'
    content = render_feed(root, base_url)
    if not output.exists() or output.read_bytes() != content:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
