"""Build/check/serve Zola with a generated portfolio feed. Requires Python 3.11+."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from portfolio_feed import prepare_feed, git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'serve', 'check'))
    parser.add_argument('--zola', default=None)
    parser.add_argument('--base-url')
    parser.add_argument('--port', type=int, default=1111)
    parser.add_argument('--interface', default='127.0.0.1')
    args, extra = parser.parse_known_args()
    root = Path(__file__).resolve().parent.parent
    zola = args.zola or (str(root / '.cache/bin/zola') if (root / '.cache/bin/zola').exists()
                         else shutil.which('zola'))
    if not zola:
        parser.error('Install Zola with bash scripts/install-zola.sh, or pass --zola PATH.')
    configured = tomllib.loads((root / 'zola.toml').read_text())['base_url']
    base_url = args.base_url or (f'http://127.0.0.1:{args.port}' if args.command == 'serve' else configured)
    prepare_feed(root, base_url)
    command = [zola, args.command]
    if args.command == 'build':
        command.extend(['--base-url', base_url])
    elif args.command == 'serve':
        command.extend(['--base-url', base_url, '--port', str(args.port), '--interface', args.interface,
                        '--extra-watch-path', 'data'])
    command.extend(extra)
    if args.command != 'serve':
        return subprocess.call(command, cwd=root)

    def signature():
        return ((root / 'data/portfolio.toml').read_bytes(), git(root, 'rev-parse', 'HEAD'))

    previous = signature()
    process = subprocess.Popen(command, cwd=root)
    try:
        while process.poll() is None:
            try:
                return process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    current = signature()
                    if current != previous:
                        prepare_feed(root, base_url)
                        previous = current
                except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
                    # Allow the editor to finish a partial TOML edit, then retry.
                    print(f'Portfolio feed: {error}', file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
