"""Verify a deployed artifact on a specific server, independent of public DNS."""
import argparse
from email.parser import BytesParser
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


class RobotsMeta(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.noindex = False
        self.feed(body.decode('utf-8', errors='replace'))

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'meta' and (attributes.get('name') or '').lower() in ('robots', 'googlebot'):
            if re.search(r'\b(?:noindex|none)\b', (attributes.get('content') or '').lower()):
                self.noindex = True


def fetch(url, connect_ip=None):
    if connect_ip is not None:
        # Keep the real hostname for the Host header and certificate verification,
        # but connect directly to this target, including on redirect to HTTPS.
        address = str(ipaddress.IPv4Address(connect_ip))
        host = urlsplit(url).hostname
        port = urlsplit(url).port
        ports = {80, 443}
        if port:
            ports.add(port)
        with tempfile.TemporaryDirectory(prefix='jmporch-http-check-') as temporary:
            body_file = Path(temporary) / 'body'
            header_file = Path(temporary) / 'headers'
            command = [
                'curl', '--silent', '--show-error', '--location', '--max-redirs', '5',
                '--connect-timeout', '10', '--max-time', '20', '--noproxy', '*',
                '--proto', '=http,https', '--proto-redir', '=https',
                '--header', 'Cache-Control: no-cache',
                '--user-agent', 'jmporch-deployment-check',
                '--output', str(body_file), '--dump-header', str(header_file),
                '--write-out', '%{json}',
            ]
            for destination_port in sorted(ports):
                command.extend(['--resolve', f'{host}:{destination_port}:{address}'])
            result = subprocess.run(command + [url], capture_output=True, text=True)
            if result.returncode:
                raise OSError(result.stderr.strip() or f'curl exited {result.returncode}')
            metadata = json.loads(result.stdout)
            if metadata['remote_ip'] != address:
                raise ValueError(f'Request reached {metadata["remote_ip"]}, expected {address}.')
            blocks = [block for block in header_file.read_bytes().split(b'\r\n\r\n')
                      if block.startswith(b'HTTP/')]
            if not blocks:
                raise ValueError('The server did not return HTTP response headers.')
            headers = BytesParser().parsebytes(blocks[-1].partition(b'\r\n')[2] + b'\r\n\r\n')
            return metadata['http_code'], headers, body_file.read_bytes(), metadata['url_effective']
    request = Request(url, headers={'Cache-Control': 'no-cache', 'User-Agent': 'jmporch-deployment-check'})
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read(), response.url


def check_server(base_url, commit, release, *, connect_ip=None, artifact=None,
                 require_noindex=False, require_indexable=False, check_haywire=False):
    base_url = base_url.rstrip('/')
    base = urlsplit(base_url)
    if base.scheme != 'https' or not base.netloc or base.path or base.query or base.fragment:
        raise ValueError('Supply an HTTPS origin without a path, query, or fragment.')
    if require_noindex and require_indexable:
        raise ValueError('Indexing requirements cannot conflict.')
    if connect_ip is not None:
        ipaddress.IPv4Address(connect_ip)
    if artifact is not None:
        artifact = Path(artifact)
        if not artifact.is_dir():
            raise ValueError('The checked deployment artifact directory does not exist.')

    def request(url):
        return fetch(url, connect_ip=connect_ip)

    def get(path, expected_status=200, content_type=None):
        status, headers, body, final_url = request(base_url + path)
        final = urlsplit(final_url)
        if (final.scheme, final.netloc, final.path) != ('https', base.netloc, urlsplit(path).path):
            raise ValueError(f'{path}: unexpected redirect to {final_url}')
        if status != expected_status:
            raise ValueError(f'{path}: expected HTTP {expected_status}, received {status}')
        if content_type and headers.get('Content-Type', '').split(';')[0].lower() != content_type:
            raise ValueError(f'{path}: expected {content_type}, received {headers.get("Content-Type")}')
        if require_indexable and expected_status == 200:
            if re.search(r'\b(?:noindex|none)\b', headers.get('X-Robots-Tag', '').lower()):
                raise ValueError(f'{path}: production must be indexable; X-Robots-Tag blocks indexing.')
            if content_type == 'text/html' and RobotsMeta(body).noindex:
                raise ValueError(f'{path}: production must be indexable; an HTML meta tag blocks indexing.')
        if artifact is not None and expected_status == 200 and not path.startswith('/haywire/'):
            relative = urlsplit(path).path.lstrip('/') or 'index.html'
            if relative.endswith('/'):
                relative += 'index.html'
            local = artifact / relative
            if not local.is_file():
                raise ValueError(f'{path}: missing from the checked deployment artifact.')
            if body != local.read_bytes():
                raise ValueError(f'{path}: response differs from the checked deployment artifact.')
        return headers, body

    query = urlencode({'release': release})
    _, marker_body = get('/deployment.json?' + query, content_type='application/json')
    marker = json.loads(marker_body)
    if marker.get('commit') != commit or marker.get('release') != release:
        raise ValueError('The public release marker does not match this workflow run.')

    headers, home = get('/', content_type='text/html')
    if require_noindex and 'noindex' not in headers.get('X-Robots-Tag', '').lower():
        raise ValueError('The test site must retain its noindex response header.')
    if b'id="portfolio"' not in home:
        raise ValueError('The main URL did not return the portfolio homepage.')
    get('/blog/', content_type='text/html')
    _, feed = get('/feed.xml')
    ET.fromstring(feed)
    get('/style.css', content_type='text/css')
    get('/enhancements.css', content_type='text/css')
    get('/main.js')
    get('/content/Jesse_Porch_240x240.jpg', content_type='image/jpeg')

    # The new main deployment must leave the independently served archive working.
    if check_haywire:
        get('/haywire/', content_type='text/html')
        status, _, _, final_url = request(base_url + '/haywire')
        if status != 200 or final_url != base_url + '/haywire/':
            raise ValueError('The Haywire directory redirect did not reach /haywire/.')
        get('/haywire/__deployment_check_missing_' + release, expected_status=404)
    get('/__deployment_check_missing_' + release, expected_status=404)

    status, _, _, final_url = request('http://' + base.netloc + '/')
    if status != 200 or final_url != base_url + '/':
        raise ValueError('HTTP did not redirect to the HTTPS homepage.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--connect-ip', help='Connect to this IPv4 address while retaining HTTPS hostname validation.')
    parser.add_argument('--artifact', type=Path, help='Compare served files to this checked build directory.')
    indexing = parser.add_mutually_exclusive_group()
    indexing.add_argument('--require-noindex', action='store_true')
    indexing.add_argument('--require-indexable', action='store_true')
    parser.add_argument('--check-haywire', action='store_true')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--release', required=True)
    arguments = parser.parse_args()
    for attempt in range(1, 4):
        try:
            check_server(arguments.base_url, arguments.commit, arguments.release,
                         connect_ip=arguments.connect_ip, artifact=arguments.artifact,
                         require_noindex=arguments.require_noindex, require_indexable=arguments.require_indexable,
                         check_haywire=arguments.check_haywire)
            print(f'PASS: {arguments.base_url} at {arguments.connect_ip or "public DNS"}, release {arguments.release}, pages, assets, feeds, redirects, and 404s.')
            return 0
        except (ValueError, ET.ParseError, URLError, TimeoutError, OSError) as error:
            print(f'Public check {attempt}/3 failed: {error}', flush=True)
            if attempt < 3:
                time.sleep(5)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
