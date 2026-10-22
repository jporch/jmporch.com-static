"""Add missing Portfolio mirrors. Install mirror-requirements.txt first.

Usage: python scripts/archive_portfolio.py [--reuse /path/to/results.json]
Existing mirror pages and their assets are left untouched and never fetched.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tomllib
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from markdownify import markdownify

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache/portfolio-mirror'
CAPTURED = datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(['curl', '--location', '--silent', '--show-error', '--max-time', '60',
                        '--output', str(dest), '--write-out', '%{json}', url], capture_output=True, text=True)
    try:
        meta = json.loads(p.stdout)
    except ValueError:
        meta = {}
    return dict(url=url, status=meta.get('http_code', 0), final_url=meta.get('url_effective', url),
                type=meta.get('content_type', ''), path=str(dest), error=p.stderr.strip())


def crawl(item):
    attempts = []
    if item.get('mirror_text'):
        return {**item, 'attempts': attempts}
    for n, url in enumerate(dict.fromkeys([item['url'], item['url'].replace('http:', 'https:')])):
        attempts.append(fetch(url, CACHE / f"{item['id']}-{n}.body"))
        if attempts[-1]['status'] == 200:
            break
    if item.get('mirror_url'):
        attempts.append(fetch(item['mirror_url'], CACHE / f"{item['id']}-mirror.body"))
    return {**{k: str(v) for k, v in item.items()}, 'attempts': attempts}


def metadata(fields):
    return '+++\n' + '\n'.join(f'{k} = {json.dumps(v, ensure_ascii=False)}' for k, v in fields.items()) + '\n+++\n\n'


def archive(item):
    r = item['attempts'][-1] if item['attempts'] else {'status': None}
    title = BeautifulSoup(item['label'], 'html.parser').get_text()
    record = dict(id=item['id'], title=title, source_url=item['url'], attempts=[{k:v for k,v in a.items() if k!='path'} for a in item['attempts']], captured=CAPTURED)
    if item.get('mirror_text'):
        source = ROOT / item['mirror_text']
        raw = source.read_bytes()
        source_url, body = raw.decode('utf-8-sig').replace('\r\n', '\n').split('\n', 1)
        if source_url.strip() != item['url']:
            raise ValueError(f'Supplied text URL does not match {item["id"]}')
        body = body.strip()
        fields = {'title': title.split(' | ')[0], 'template': 'mirror.html',
                  'extra.source_url': item['url'], 'extra.captured': CAPTURED,
                  'extra.kind': 'article', 'extra.source_type': 'supplied-text',
                  'extra.source_text': source.name}
        # Plain text stays literal, including punctuation and original wording.
        paragraphs = ['<p>' + html.escape(p) + '</p>' for p in body.split('\n\n') if p.strip()]
        (source.parent / 'index.md').write_text(metadata(fields) + '\n\n'.join(paragraphs) + '\n')
        record.update(status='mirrored', kind='article', source_type='supplied-text',
                      source_file=source.name, source_sha256=hashlib.sha256(raw).hexdigest(),
                      text_characters=len(body), mirror_path=f"/mirror/{item['id']}/", assets=[])
        record['original_unavailable'] = bool(item['attempts']) and r['status'] != 200
        if record['original_unavailable']:
            record['reason'] = f"Original last checked: HTTP {r['status']}. Recovered from owner-supplied text."
        return record
    if r['status'] != 200:
        record.update(status='unavailable', reason='Site says: This website is not available (HTTP 403).' if 'trinityfellowsacademy' in r['url'] else f"HTTP {r['status']}; intended document unavailable.")
        return record
    folder = ROOT / 'content/mirror' / item['id']
    folder.mkdir(parents=True, exist_ok=True)
    raw = Path(r['path']).read_bytes()
    record['source_sha256'] = hashlib.sha256(raw).hexdigest()
    assets = []
    def asset(url, name=None):
        name = name or (hashlib.sha256(url.encode()).hexdigest()[:12] + Path(urlparse(url).path).suffix.lower())
        response = fetch(url, folder / name)
        info = {k:v for k,v in response.items() if k!='path'}
        info['file'] = name
        if response['status']==200:
            info['sha256'] = hashlib.sha256((folder/name).read_bytes()).hexdigest()
        else:
            (folder/name).unlink(missing_ok=True)
        assets.append(info)
        return name if response['status']==200 else None
    if 'pdf' in (r['type'] or ''):
        assert raw.startswith(b'%PDF-'), 'Response is not a PDF'
        (folder/'document.pdf').write_bytes(raw)
        body = '[Read or download the preserved PDF](document.pdf).\n'
        if item.get('mirror_note'):
            body = item['mirror_note'] + '\n\n' + body
            record['version_note'] = item['mirror_note']
        if item.get('mirror_url'):
            body += f"\n[Source of the preserved copy]({item['mirror_url']}).\n"
            record['mirror_source_url'] = item['mirror_url']
        record['kind'] = 'pdf'
    else:
        (folder/'source.html.txt').write_bytes(raw)
        soup = BeautifulSoup(raw, 'html.parser')
        record['page_title'] = soup.title.get_text(' ',strip=True) if soup.title else ''
        if item['id']=='tangere':
            body = 'The original project URL serves a directory of Tangere source releases, licenses, and image archives. The files below are preserved locally.\n\n'
            for a in soup.select('a[href]'):
                href = a['href']
                if href.endswith(('.tgz','.txt')):
                    name = asset(urljoin(r['final_url'],href), Path(urlparse(href).path).name)
                    if name: body += f'- [{name}]({name})\n'
            record['kind'] = 'software-directory'
        else:
            host = urlparse(r['final_url']).hostname or ''
            selector = item.get('mirror_selector') or ('.post_body' if host == 'itch.io' else '.pg-content__text' if host == 'christandpopculture.com' else '.entry-content')
            article = soup.select_one(selector)
            assert article and len(article.get_text())>2000, f"Missing/full article check failed: {title}"
            if item['id']=='sheathing-the-sword':
                promo=article.select_one('div.clearfix')
                if promo: promo.decompose()
            # Include the article's featured image, independent of theme markup.
            featured = soup.select_one('.post-content > img, .pg-content__featured, article .entry-thumbnail')
            if featured: article.insert(0, featured.extract())
            for tag in article.select('script,style,form,iframe,button'):
                tag.decompose()
            for tag in article.find_all(True):
                for attr in list(tag.attrs):
                    if attr.startswith('on') or attr in ('style','class','srcset','sizes'): del tag[attr]
                if tag.has_attr('href'):
                    tag['href']=urljoin(r['final_url'],tag['href'])
                    if urlparse(tag['href']).scheme not in ('http','https','mailto'): del tag['href']
            for img in article.select('img'):
                source=urljoin(r['final_url'],img.get('src',''))
                local=asset(source)
                if local: img['src']=local
                else: img.replace_with(f"[Image unavailable at capture: {img.get('alt','')}]")
            record['text_characters']=len(article.get_text())
            body=markdownify(str(article), heading_style='ATX')
            record['kind']='article'
        record['raw_response']='source.html.txt'
    record.update(status='mirrored', mirror_path=f"/mirror/{item['id']}/", assets=assets)
    fields={'title':title.split(' | ')[0], 'template':'mirror.html', 'extra.source_url':item['url'],
            'extra.resolved_url':r['final_url'], 'extra.captured':CAPTURED, 'extra.kind':record['kind']}
    if record.get('raw_response'): fields['extra.raw_response']=record['raw_response']
    (folder/'index.md').write_text(metadata(fields)+body+'\n')
    return record


def regenerate(reuse=None):
    items = [i for g in tomllib.loads((ROOT/'data/portfolio.toml').read_text())['groups']
             for i in g['items'] if 'url' in i]
    dest = ROOT/'static/mirror/audit.json'
    audit = json.loads(dest.read_text()) if dest.exists() else {'items': []}
    records = {i['id']: i for i in audit['items']}
    cached = {i['id']: i for i in json.loads(reuse.read_text())} if reuse else {}
    pending = [i for i in items if not (ROOT/'content/mirror'/i['id']/'index.md').is_file()]

    def add(item):
        captured = {**cached[item['id']], **item} if item['id'] in cached else crawl(item)
        try:
            return archive(captured)
        except (AssertionError, ValueError, OSError) as error:
            # Keep unsupported or unavailable sources out of the public index.
            # Retain diagnostics internally so a later invocation can retry.
            return {'id': item['id'], 'source_url': item['url'], 'status': 'unavailable',
                    'captured': CAPTURED, 'reason': str(error),
                    'attempts': [{k: v for k, v in a.items() if k != 'path'}
                                 for a in captured['attempts']]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for record in pool.map(add, pending):
            records[record['id']] = record
    audit.update(generated=CAPTURED, items=list(records.values()))
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse', type=Path)
    args = parser.parse_args()
    regenerate(args.reuse)
    print('Mirror regenerated.')

if __name__=='__main__': main()
