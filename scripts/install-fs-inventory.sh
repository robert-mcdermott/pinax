#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert McDermott
#
# Installs the pinned fs-inventory release that Pinax is tested with into ./bin (or --install-dir).
# Downloads the binary for this computer and SHA256SUMS from the GitHub release, verifies the
# checksum and the binary's version, then installs it. Never uses sudo.
#
#   scripts/install-fs-inventory.sh [--version v1.0.0] [--install-dir DIR]
set -eu
umask 022

REPOSITORY=robert-mcdermott/fs-inventory
VERSION=${FS_INVENTORY_VERSION:-v1.0.0}
INSTALL_DIR=$(cd "$(dirname "$0")/.." && pwd)/bin

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) VERSION=$2; shift 2 ;;
    --install-dir) INSTALL_DIR=$2; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$VERSION" in v*) ;; *) VERSION="v$VERSION" ;; esac

case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) echo "error: unsupported OS" >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) echo "error: unsupported CPU" >&2; exit 1 ;; esac
ASSET="fs-inventory-$os-$arch"
BASE="https://github.com/$REPOSITORY/releases/download/$VERSION"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' 0
fetch() { curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 3 --output "$2" "$1"; }

echo "==> Downloading $ASSET $VERSION"
fetch "$BASE/$ASSET" "$TMP/$ASSET"
fetch "$BASE/SHA256SUMS" "$TMP/SHA256SUMS"

EXPECTED=$(awk -v f="$ASSET" '$2 == f || $2 == "*" f { print $1 }' "$TMP/SHA256SUMS")
[ "${#EXPECTED}" -eq 64 ] || { echo "error: no checksum for $ASSET in SHA256SUMS" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then ACTUAL=$(sha256sum "$TMP/$ASSET" | awk '{ print $1 }')
else ACTUAL=$(shasum -a 256 "$TMP/$ASSET" | awk '{ print $1 }'); fi
[ "$ACTUAL" = "$EXPECTED" ] || { echo "error: checksum mismatch for $ASSET" >&2; exit 1; }
echo "==> Checksum verified"

chmod 755 "$TMP/$ASSET"
GOT=$("$TMP/$ASSET" --version | awk '{ print $2 }')
[ "v$GOT" = "$VERSION" ] || { echo "error: binary reports version $GOT, expected $VERSION" >&2; exit 1; }

mkdir -p "$INSTALL_DIR"
mv -f "$TMP/$ASSET" "$INSTALL_DIR/fs-inventory"
echo "==> Installed $INSTALL_DIR/fs-inventory ($("$INSTALL_DIR/fs-inventory" --version))"
