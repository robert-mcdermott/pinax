#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Try sign-in with an identity provider on a laptop, with Dex standing in for Entra ID (docs/ENTRA-ID.md):
#
#   scripts/dex/dex.sh up     start Dex on http://localhost:5556 and print Pinax's settings
#   scripts/dex/dex.sh down   stop and remove it
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
NAME=pinax-dex
IMAGE=ghcr.io/dexidp/dex:v2.46.1

case ${1:-up} in
    up)
        podman rm -f "$NAME" >/dev/null 2>&1 || true
        podman run -d --name "$NAME" -p 127.0.0.1:5556:5556 -v "$HERE/dex.yaml:/etc/dex/config.yaml:ro" \
            "$IMAGE" dex serve /etc/dex/config.yaml >/dev/null
        for _ in $(seq 1 30); do
            curl -fsS http://localhost:5556/dex/.well-known/openid-configuration >/dev/null 2>&1 && break
            sleep 1
        done
        curl -fsS http://localhost:5556/dex/.well-known/openid-configuration >/dev/null
        cat <<SETTINGS
Dex is running at http://localhost:5556/dex. Seed the demo (uv run pinax seed), then run Pinax on port 8001 with:

PINAX_AUTH_MODE=oidc
PINAX_OIDC_ISSUER=http://localhost:5556/dex
PINAX_OIDC_CLIENT_ID=pinax-local
PINAX_OIDC_CLIENT_SECRET=pinax-local-secret
PINAX_OIDC_USERNAME_CLAIM=email
PINAX_OIDC_USERNAME_DOMAINS=example.org
PINAX_PUBLIC_URL=http://localhost:8001
PINAX_SESSION_SECRET=$(openssl rand -hex 32)

Open http://localhost:8001 (not 127.0.0.1). Accounts, each with the password "password":
pnatarajan@example.org (storage administrator), afujimoto@example.org (PI), newperson@example.org (no Pinax
account), and pnatarajan@example.com (a guest named like a staff member).
SETTINGS
        ;;
    down) podman rm -f "$NAME" >/dev/null 2>&1 || true ;;
    *) sed -n '3,6p' "$0"; exit 2 ;;
esac
