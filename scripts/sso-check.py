# SPDX-License-Identifier: Apache-2.0
"""Check Pinax's header mode behind a real oauth2-proxy, with a stand-in identity provider on this machine.

    uv run python scripts/sso-check.py --oauth2-proxy /path/to/oauth2-proxy

It starts three processes on 127.0.0.1, all temporary:
- a minimal OpenID Connect provider (this script; tokens signed RS256 with the system's `openssl`) that signs in
  `jdoe@example.org` without asking;
- oauth2-proxy with deploy/oauth2-proxy/pinax.cfg, overriding only what differs here: addresses, the issuer,
  the client and cookie secrets, and plain HTTP;
- Pinax in header mode with `PINAX_AUTH_HEADER=X-Forwarded-Preferred-Username`, and a catalog holding that person.

Then it signs in through the proxy as a browser would and checks: Pinax sees the signed-in person; a username
header sent by the browser is not believed (removed or overwritten by oauth2-proxy); without a session the proxy
asks for sign-in instead of passing the request on; and /api/health needs no sign-in. Exits 0 when all pass.
"""

import argparse
import base64
import http.cookiejar
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER = {"sub": "u-1001", "email": "jdoe@example.org", "preferred_username": "jdoe@example.org", "name": "Jo Doe"}
CLIENT_ID, CLIENT_SECRET = "pinax-check", secrets.token_urlsafe(24)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Provider:
    """Just enough OpenID Connect for oauth2-proxy: discovery, authorize (signs in USER at once), token, keys."""

    def __init__(self, workdir: Path, port: int):
        self.issuer = f"http://127.0.0.1:{port}"
        self.key = workdir / "idp-key.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(self.key), "2048"], check=True, capture_output=True)
        modulus = subprocess.run(["openssl", "rsa", "-in", str(self.key), "-noout", "-modulus"], check=True,
                                 capture_output=True, text=True).stdout.strip().split("=", 1)[1]
        self.jwk = {"kty": "RSA", "kid": "check", "use": "sig", "alg": "RS256", "n": b64url(bytes.fromhex(modulus)),
                    "e": b64url((65537).to_bytes(3, "big"))}
        self.codes: dict[str, dict] = {}
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def sign(self, claims: dict) -> str:
        head = b64url(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "check"}).encode())
        body = b64url(json.dumps(claims).encode())
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key)], input=f"{head}.{body}".encode(),
                             check=True, capture_output=True).stdout
        return f"{head}.{body}.{b64url(sig)}"

    def _handler(self):
        idp = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, obj, status=200):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(url.query))
                if url.path == "/.well-known/openid-configuration":
                    self._json({"issuer": idp.issuer, "authorization_endpoint": f"{idp.issuer}/authorize",
                                "token_endpoint": f"{idp.issuer}/token", "jwks_uri": f"{idp.issuer}/jwks",
                                "userinfo_endpoint": f"{idp.issuer}/userinfo", "response_types_supported": ["code"],
                                "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
                                "scopes_supported": ["openid", "email", "profile"]})
                elif url.path == "/jwks":
                    self._json({"keys": [idp.jwk]})
                elif url.path == "/authorize":
                    code = secrets.token_urlsafe(16)
                    idp.codes[code] = {"nonce": q.get("nonce"), "client_id": q.get("client_id")}
                    target = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                elif url.path == "/userinfo":
                    self._json({**USER, "email_verified": True})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()))
                grant = idp.codes.pop(form.get("code", ""), None)
                if self.path != "/token" or grant is None:
                    self._json({"error": "invalid_grant"}, 400)
                    return
                now = int(time.time())
                claims = {"iss": idp.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 600, "email_verified": True, **USER}
                if grant.get("nonce"):
                    claims["nonce"] = grant["nonce"]
                self._json({"access_token": secrets.token_urlsafe(24), "token_type": "Bearer", "expires_in": 600,
                            "id_token": idp.sign(claims)})

        return Handler


def wait_for(url: str, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except urllib.error.HTTPError:
            return  # it answers
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"nothing answered at {url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--oauth2-proxy", required=True, help="The oauth2-proxy binary")
    args = ap.parse_args()
    if not shutil.which("openssl"):
        raise SystemExit("this check needs the openssl command")
    if not (shutil.which(args.oauth2_proxy) or Path(args.oauth2_proxy).is_file()):
        raise SystemExit(f"no oauth2-proxy at {args.oauth2_proxy}")
    work = Path(tempfile.mkdtemp(prefix="pinax-sso-"))
    pinax = str(Path(sys.executable).parent / "pinax")
    procs: list[subprocess.Popen] = []
    log = open(work / "oauth2-proxy.log", "w")  # noqa: SIM115 - closed in finally, after the process ends
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        idp_port, proxy_port, pinax_port = free_port(), free_port(), free_port()
        idp = Provider(work, idp_port)

        env = {**os.environ, "PINAX_VAR_ROOT": str(work / "var"), "PINAX_SCAN_ROOT": str(work / "scans"),
               "PINAX_ENVIRONMENT": "prod", "PINAX_AUTH_MODE": "header",
               "PINAX_AUTH_HEADER": "X-Forwarded-Preferred-Username", "PINAX_TRUSTED_PROXIES": '["127.0.0.1", "::1"]'}
        people = work / "people.csv"
        people.write_text(f"username,display_name,email\n{USER['preferred_username']},{USER['name']},{USER['email']}\n")
        subprocess.run([pinax, "import", "--people", str(people), "--apply"], env=env, check=True, capture_output=True)
        procs.append(subprocess.Popen([pinax, "serve", "--port", str(pinax_port)], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        secret_file = work / "client-secret"
        secret_file.write_text(CLIENT_SECRET)
        config = Path(__file__).parents[1] / "deploy" / "oauth2-proxy" / "pinax.cfg"
        procs.append(subprocess.Popen([
            args.oauth2_proxy, f"--config={config}", f"--http-address=127.0.0.1:{proxy_port}",
            f"--upstream=http://127.0.0.1:{pinax_port}/", f"--oidc-issuer-url={idp.issuer}", f"--client-id={CLIENT_ID}",
            f"--client-secret-file={secret_file}", f"--redirect-url=http://127.0.0.1:{proxy_port}/oauth2/callback",
            f"--cookie-secret={b64url(os.urandom(32))}", "--cookie-secure=false", "--email-domain=*",
        ], stdout=log, stderr=subprocess.STDOUT))
        wait_for(f"http://127.0.0.1:{pinax_port}/api/health")
        wait_for(f"http://127.0.0.1:{proxy_port}/ping")
        proxy = f"http://127.0.0.1:{proxy_port}"

        # A browser: cookies kept, redirects followed.
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def get(opener, path: str, headers: dict | None = None):
            req = urllib.request.Request(proxy + path, headers=headers or {})
            try:
                with opener.open(req, timeout=10) as r:
                    return r.status, r.geturl(), r.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.geturl(), e.read().decode()

        status, _, body = get(browser, "/api/health")
        check("health needs no sign-in", status == 200 and json.loads(body)["status"] in ("ok", "degraded"), body)

        status, _, body = get(browser, "/api/me")
        me = json.loads(body) if status == 200 else {}
        check("signing in through the proxy reaches Pinax as the signed-in person",
              me.get("username") == USER["preferred_username"], f"{status} {body[:200]}")

        status, _, body = get(browser, "/api/me", {"X-Forwarded-Preferred-Username": "someone-else@example.org"})
        me = json.loads(body) if status == 200 else {}
        check("a username header sent by the browser is not believed", me.get("username") == USER["preferred_username"],
              f"{status} {body[:200]}")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        stranger = urllib.request.build_opener(NoRedirect)
        status, _, body = get(stranger, "/api/me", {"X-Forwarded-Preferred-Username": USER["preferred_username"]})
        check("without a session the proxy asks for sign-in and passes nothing on", status in (302, 401, 403),
              f"{status} {body[:200]}")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log.close()
        if failures:
            print(f"oauth2-proxy log: {work / 'oauth2-proxy.log'}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("All checks passed." if not failures else f"{len(failures)} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
