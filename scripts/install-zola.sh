#!/usr/bin/env bash
# Install the pinned official binary into this checkout, without global changes.
set -euo pipefail
cd "$(dirname "$0")/.."
version="$(cat .zola-version)"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    target=aarch64-apple-darwin
    checksum=cbffbd29b3f59c3f52633507c8cb945a7a02d8b1399b43b235f5932912297aa3 ;;
  Darwin-x86_64)
    target=x86_64-apple-darwin
    checksum=79a4d0ab51a4d863c068e6e594c6fce36f0aa17429a414ea63066f5910d14460 ;;
  Linux-x86_64)
    target=x86_64-unknown-linux-gnu
    checksum=8f5132b3522412d04e395e0b25f6d68613ad272a873e54a2b3ebf664873024a4 ;;
  Linux-aarch64)
    target=aarch64-unknown-linux-gnu
    checksum=266448fffbf7c7004ca399d0e76dd699541771096d8a42aede98cebe2a029d02 ;;
  *) echo 'Unsupported platform; install Zola 0.23.6 from https://www.getzola.org/.' >&2; exit 1 ;;
esac
if [ "$version" != '0.23.6' ]; then
  echo 'Update the installer checksums when changing .zola-version.' >&2
  exit 1
fi
mkdir -p .cache/bin
if [ -x .cache/bin/zola ] && [ "$(.cache/bin/zola --version)" = "zola $version" ]; then
  exit 0
fi
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
curl --fail --location --silent --show-error --retry 3 \
  "https://github.com/getzola/zola/releases/download/v$version/zola-v$version-$target.tar.gz" \
  --output "$temporary/zola.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$temporary/zola.tar.gz" | cut -d ' ' -f 1)"
else
  actual="$(shasum -a 256 "$temporary/zola.tar.gz" | cut -d ' ' -f 1)"
fi
if [ "$actual" != "$checksum" ]; then
  echo 'Zola archive checksum mismatch.' >&2
  exit 1
fi
tar -xzf "$temporary/zola.tar.gz" -C "$temporary" zola
cp "$temporary/zola" .cache/bin/zola
.cache/bin/zola --version
