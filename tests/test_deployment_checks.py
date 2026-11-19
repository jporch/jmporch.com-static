"""Public checks must reject stale releases and broken path routing."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_deployment import check_server, fetch


BASE = 'https://test.jmporch.com'
COMMIT = 'a' * 40
RELEASE = COMMIT + '-123-1'


class TestPublicDeployment(unittest.TestCase):
    def response(self, url):
        path = urlsplit(url).path
        headers = {'Content-Type': 'text/html', 'X-Robots-Tag': 'noindex, nofollow'}
        body = b'<html><div id="portfolio"></div></html>'
        status = 200
        if path == '/deployment.json':
            headers['Content-Type'] = 'application/json'
            body = json.dumps({'commit': COMMIT, 'release': RELEASE}).encode()
        elif path == '/feed.xml':
            headers['Content-Type'] = 'application/xml'
            body = b'<rss><channel/></rss>'
        elif path.endswith('.css'):
            headers['Content-Type'] = 'text/css'
            body = b'body { color: black; }'
        elif path.endswith('.jpg'):
            headers['Content-Type'] = 'image/jpeg'
            body = b'jpeg'
        elif path.endswith('.js'):
            headers['Content-Type'] = 'application/javascript'
            body = b'console.log("test");'
        elif '__deployment_check_missing_' in path:
            status = 404
        if url.startswith('http:'):
            url = BASE + '/'
        elif path == '/haywire':
            url = BASE + '/haywire/'
        return status, headers, body, url

    def check_with(self, override=None, **options):
        def fetch(url, **kwargs):
            response = self.response(url)
            return override(url, response) if override else response
        settings = dict(require_noindex=True, check_haywire=True)
        settings.update(options)
        with patch('check_deployment.fetch', side_effect=fetch):
            check_server(BASE, COMMIT, RELEASE, **settings)

    def test_healthy_release(self):
        self.check_with()

    def test_same_commit_from_an_older_run_is_rejected(self):
        def stale(url, response):
            status, headers, body, final_url = response
            if urlsplit(url).path == '/deployment.json':
                body = json.dumps({'commit': COMMIT, 'release': 'older-run'}).encode()
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'marker'):
            self.check_with(stale)

    def test_main_homepage_cannot_replace_haywire(self):
        def wrong_route(url, response):
            status, headers, body, final_url = response
            if urlsplit(url).path == '/haywire/':
                final_url = BASE + '/'
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'redirect'):
            self.check_with(wrong_route)

    def test_spa_fallback_cannot_hide_missing_archive_files(self):
        def soft_404(url, response):
            status, headers, body, final_url = response
            if '/haywire/__deployment_check_missing_' in url:
                status = 200
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'expected HTTP 404'):
            self.check_with(soft_404)

    def test_test_site_must_remain_noindex(self):
        def indexable(url, response):
            status, headers, body, final_url = response
            if url == BASE + '/':
                headers.pop('X-Robots-Tag')
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'noindex'):
            self.check_with(indexable)

    def test_html_returned_instead_of_stylesheet_is_rejected(self):
        def wrong_mime(url, response):
            status, headers, body, final_url = response
            if url.endswith('/style.css'):
                headers['Content-Type'] = 'text/html'
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'text/css'):
            self.check_with(wrong_mime)

    def test_http_must_redirect_to_https(self):
        def no_redirect(url, response):
            status, headers, body, final_url = response
            if url.startswith('http:'):
                final_url = url
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'HTTP did not redirect'):
            self.check_with(no_redirect)

    def test_cross_origin_redirect_is_rejected(self):
        def redirect(url, response):
            status, headers, body, final_url = response
            if url.endswith('/blog/'):
                final_url = 'https://jmporch.com/blog/'
            return status, headers, body, final_url
        with self.assertRaisesRegex(ValueError, 'unexpected redirect'):
            self.check_with(redirect)

    def test_old_server_does_not_require_haywire_or_noindex(self):
        def old_site(url, response):
            self.assertNotIn('/haywire', url)
            status, headers, body, final_url = response
            headers.pop('X-Robots-Tag', None)
            return status, headers, body, final_url
        self.check_with(old_site, require_noindex=False, check_haywire=False)

    def test_served_asset_must_match_the_shared_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ('/deployment.json', '/', '/blog/', '/feed.xml', '/style.css',
                         '/enhancements.css', '/main.js', '/content/Jesse_Porch_240x240.jpg'):
                relative = path.lstrip('/') or 'index.html'
                if relative.endswith('/'):
                    relative += 'index.html'
                destination = Path(directory) / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(self.response(BASE + path)[2])
            (Path(directory) / 'style.css').write_text('different stylesheet')
            with self.assertRaisesRegex(ValueError, 'differs from the checked deployment artifact'):
                self.check_with(artifact=directory)

    def test_each_request_is_pinned_to_the_target_ip(self):
        def fetch(url, **kwargs):
            self.assertEqual(kwargs['connect_ip'], '23.92.22.231')
            return self.response(url)
        with patch('check_deployment.fetch', side_effect=fetch) as mocked:
            check_server(BASE, COMMIT, RELEASE, connect_ip='23.92.22.231')
        self.assertGreater(mocked.call_count, 8)


class TestPinnedTransport(unittest.TestCase):
    def test_pinning_preserves_host_and_handles_404_headers(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('X-Robots-Tag', 'noindex')
                self.end_headers()
                self.wfile.write(self.headers['Host'].encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            authority = f'deployment.invalid:{server.server_port}'
            url = f'http://{authority}/missing'
            status, headers, body, final_url = fetch(url, connect_ip='127.0.0.1')
            self.assertEqual(status, 404)
            self.assertEqual(headers.get('Content-Type'), 'text/plain')
            self.assertEqual(headers.get('X-Robots-Tag'), 'noindex')
            self.assertEqual(body, authority.encode())
            self.assertEqual(final_url, url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
