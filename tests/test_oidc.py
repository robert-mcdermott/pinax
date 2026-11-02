# SPDX-License-Identifier: Apache-2.0
"""Sign-in with Entra ID in Pinax itself (PINAX_AUTH_MODE=oidc), against a stand-in for Entra ID.

The stand-in is an OpenID Connect provider in miniature: it publishes its signing keys, checks the client secret and
the PKCE verifier, and signs ID tokens. Pinax reaches it through httpx2's ASGI transport, so the whole sign-in runs
in the test, as it will against Entra ID. Following Tundra's tests of the same.
"""

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from conftest import login
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from pinax import config, oidc
from pinax.config import Settings

ISSUER = "https://login.example.test/tenant-id/v2.0"
CLIENT_ID, CLIENT_SECRET = "pinax-test", "a-client-secret"
PUBLIC_URL = "https://pinax.test"


class StandInProvider:
    """The part Entra ID plays: discovery, signing keys, the token endpoint, and the sign-in itself."""

    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "key-1", "use": "sig", "alg": "RS256"})
        self.pending: dict[str, dict] = {}  # code -> what the sign-in established
        self.app = Starlette(routes=[
            Route("/tenant-id/v2.0/.well-known/openid-configuration", self.discovery),
            Route("/keys", self.keys),
            Route("/token", self.token, methods=["POST"]),
        ])

    def discovery(self, _request: Request) -> JSONResponse:
        return JSONResponse({
            "issuer": ISSUER,
            "authorization_endpoint": "https://login.example.test/authorize",
            "token_endpoint": "https://login.example.test/token",
            "jwks_uri": "https://login.example.test/keys",
            "end_session_endpoint": "https://login.example.test/logout",
            "response_types_supported": ["code"],
            "subject_types_supported": ["pairwise"],
            "id_token_signing_alg_values_supported": ["RS256"],
        })

    def keys(self, _request: Request) -> JSONResponse:
        return JSONResponse({"keys": [self.key.as_dict(private=False)]})

    async def token(self, request: Request) -> JSONResponse:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}  # urlencoded, as OAuth sends
        sign_in = self.pending.pop(form.get("code", ""), None)
        if sign_in is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        verifier = form.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if challenge != sign_in["code_challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
        return JSONResponse({"access_token": "not-used", "token_type": "Bearer", "expires_in": 3600,
                             "id_token": jwt.encode({"alg": "RS256", "kid": sign_in["kid"]}, sign_in["claims"],
                                                    sign_in["key"])})

    def sign_in(self, authorize_url: str, key: RSAKey | None = None, **claims) -> str:
        """Someone signs in at the provider; returns the address the provider sends the browser back to."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert query["client_id"] == CLIENT_ID and query["response_type"] == "code"
        assert query["redirect_uri"] == f"{PUBLIC_URL}/auth/callback" and "openid" in query["scope"]
        assert query["code_challenge_method"] == "S256"
        now = int(time.time())
        code = secrets.token_urlsafe(16)
        self.pending[code] = {
            "code_challenge": query["code_challenge"], "key": key or self.key, "kid": "key-1",
            "claims": {"iss": ISSUER, "aud": CLIENT_ID, "sub": secrets.token_hex(8), "iat": now, "nbf": now,
                       "exp": now + 600, "nonce": query["nonce"], "name": "Someone", **claims},
        }
        return f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"


def _oidc_settings(monkeypatch, **extra):
    for name, value in {"AUTH_MODE": "oidc", "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_ID": CLIENT_ID,
                        "OIDC_CLIENT_SECRET": CLIENT_SECRET, "OIDC_USERNAME_DOMAINS": "example.org",
                        "PUBLIC_URL": PUBLIC_URL, "SESSION_SECRET": "s" * 40, **extra}.items():
        monkeypatch.setenv(f"PINAX_{name}", value)
    config.get_settings.cache_clear()


@pytest.fixture
def provider(catalog, monkeypatch):
    stand_in = StandInProvider()
    _oidc_settings(monkeypatch)
    monkeypatch.setattr(oidc, "HTTP_TRANSPORT", httpx2.ASGITransport(app=stand_in.app))
    monkeypatch.setattr(oidc, "_clients", {})
    return stand_in


@pytest.fixture
def browser(provider):
    from pinax.main import app

    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield client


def sign_in(browser, provider, next_path="/app/#/explore", **claims):
    """The whole round trip: Pinax, the provider's sign-in page, and back to Pinax."""
    to_provider = browser.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    assert to_provider.status_code == 302
    assert to_provider.headers["location"].startswith("https://login.example.test/authorize?")
    back = provider.sign_in(to_provider.headers["location"], **claims)
    return browser.get(urlsplit(back).path + "?" + urlsplit(back).query, follow_redirects=False)


def _events(action: str) -> list[dict]:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [e.detail for e in s.query(AuditEvent).filter_by(action=action).order_by(AuditEvent.id)]


def _set(username: str, **values) -> None:
    from pinax.db import session_scope
    from pinax.models import User

    with session_scope() as s:
        u = s.query(User).filter_by(username=username).one()
        for k, v in values.items():
            setattr(u, k, v)


def test_people_sign_in_and_out_through_the_provider(browser, provider):
    assert browser.get("/api/me").status_code == 401
    done = sign_in(browser, provider, preferred_username="Ada@Example.org")
    assert done.status_code == 303 and done.headers["location"] == "/app/#/explore"
    cookies = done.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("pinax_session="))
    assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    me = browser.get("/api/me").json()
    assert me["username"] == "ada" and me["last_login_at"]
    assert browser.get("/api/home").status_code == 200
    signed_in = _events("auth.signed_in")[-1]
    assert signed_in["method"] == "oidc" and signed_in["identity"] == "Ada@Example.org"  # as the provider said it

    out = browser.get("/auth/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"].startswith("https://login.example.test/logout?")
    assert parse_qs(urlsplit(out.headers["location"]).query)["post_logout_redirect_uri"] == [f"{PUBLIC_URL}/auth/signed-out"]
    assert browser.get("/api/me").status_code == 401
    assert _events("auth.signed_out")[-1]["method"] == "oidc"
    assert "signed out" in browser.get("/auth/signed-out").text


@pytest.mark.parametrize("claimed, message, reason", [
    ("newcomer@example.org", "Pinax has no account for you yet", "no Pinax account"),
    ("ada@example.com", "not one Pinax accepts", "not an allowed domain"),  # a guest named like a staff member
    ("ada", "not one Pinax accepts", "not an allowed domain"),
])
def test_people_without_an_account_are_refused(browser, provider, claimed, message, reason):
    refused = sign_in(browser, provider, preferred_username=claimed)
    assert refused.status_code == 403 and message in refused.text and claimed in refused.text
    assert browser.get("/api/me").status_code == 401
    event = _events("auth.sign_in_refused")[-1]
    assert event["reason"] == reason and event["identity"] == claimed and event["method"] == "oidc"


def test_deactivated_people_are_refused_and_lose_their_session(browser, provider):
    assert sign_in(browser, provider, preferred_username="ada@example.org").status_code == 303
    assert browser.get("/api/me").status_code == 200
    _set("ada", active=False)
    assert browser.get("/api/me").status_code == 401  # at once, despite the session cookie
    refused = sign_in(browser, provider, preferred_username="ada@example.org")
    assert refused.status_code == 403 and "deactivated" in refused.text


def test_tokens_that_do_not_check_out_are_refused(browser, provider):
    forged = RSAKey.generate_key(2048, parameters={"kid": "key-1"})
    expired = {"iat": int(time.time()) - 1200, "nbf": int(time.time()) - 1200, "exp": int(time.time()) - 600}
    # (a token up to two minutes past its expiry is accepted, the usual allowance for clocks that differ)
    for claims in ({"key": forged}, {"aud": "another-app"}, {"iss": "https://login.example.test/other/v2.0"}, expired):
        refused = sign_in(browser, provider, preferred_username="ada@example.org", **claims)
        assert refused.status_code == 403 and "could not confirm the sign-in" in refused.text, claims
        assert browser.get("/api/me").status_code == 401


def test_a_sign_in_cannot_be_replayed_or_finished_in_another_browser(browser, provider):
    from pinax.main import app

    to_provider = browser.get("/auth/login", follow_redirects=False).headers["location"]
    back = provider.sign_in(to_provider, preferred_username="ada@example.org")
    path = urlsplit(back).path + "?" + urlsplit(back).query
    with TestClient(app, base_url=PUBLIC_URL) as other:  # no cookie holding this sign-in's state
        assert other.get(path, follow_redirects=False).status_code == 403
        assert other.get("/api/me").status_code == 401
    assert browser.get(path, follow_redirects=False).status_code == 303  # the browser that started it
    assert browser.get(path, follow_redirects=False).status_code == 403  # and only once


def test_the_providers_error_is_shown(browser):
    page = browser.get("/auth/callback", params={"error": "access_denied",
                                                  "error_description": "AADSTS50105: not assigned to the application"})
    assert page.status_code == 403 and "AADSTS50105" in page.text


def test_sign_in_returns_only_to_pages_of_pinax(browser, provider):
    for target in ("https://elsewhere.example/", "//elsewhere.example/", "/\\elsewhere.example"):
        done = sign_in(browser, provider, next_path=target, preferred_username="ada@example.org")
        assert done.headers["location"] == "/app/"


def test_a_username_matches_whole_or_without_the_allowed_domain(browser, provider):
    from pinax.db import session_scope
    from pinax.models import User

    with session_scope() as s:
        s.add(User(username="Zoe@Example.org", display_name="Zoe", email="zoe@example.org"))
    assert sign_in(browser, provider, preferred_username="zoe@example.org").status_code == 303
    assert browser.get("/api/me").json()["username"] == "Zoe@Example.org"  # the person named by their UPN
    assert sign_in(browser, provider, preferred_username="IVY@example.org").status_code == 303
    assert browser.get("/api/me").json()["username"] == "ivy"  # the person named without the domain


def test_identities_and_the_allowed_domains():
    allowed = Settings(oidc_username_domains="example.org, @fhcrc.org")
    assert oidc.identity_from_claims({"preferred_username": "JDoe@Example.org"}, allowed) == ("jdoe@example.org", "jdoe")
    assert oidc.identity_from_claims({"preferred_username": "jdoe@fhcrc.org"}, allowed) == ("jdoe@fhcrc.org", "jdoe")
    for value in ("jdoe@example.com", "jdoe", "@example.org", "", None):
        assert oidc.identity_from_claims({"preferred_username": value}, allowed) is None
    exact = Settings(oidc_username_claim="email")
    assert oidc.identity_from_claims({"email": "JDoe@Example.org"}, exact) == ("jdoe@example.org", "jdoe@example.org")
    assert oidc.identity_from_claims({"preferred_username": "jdoe@example.org"}, exact) is None


def test_other_ways_in_are_closed(browser, provider):
    assert browser.get("/api/me", headers={"X-Remote-User": "ivy"}).status_code == 401
    login(browser, "ivy")  # the demo's persona cookie
    assert browser.get("/api/me").status_code == 401
    assert browser.get("/api/demo/users").status_code == 404
    assert browser.post("/api/auth/login", json={"username": "ivy", "password": "x"}).status_code == 404
    assert browser.get("/api/config").json()["auth_mode"] == "oidc"


def test_api_tokens_and_a_temporary_password_left_from_before(browser, provider):
    _set("ada", must_change_password=True)  # from local sign-in, before the site moved to Entra ID
    sign_in(browser, provider, preferred_username="ada@example.org")
    assert browser.get("/api/home").status_code == 200  # no password to change here
    made = browser.post("/api/tokens", json={"name": "Report", "days": 30}, headers={"Origin": PUBLIC_URL})
    assert made.status_code == 201
    from pinax.main import app

    with TestClient(app, base_url=PUBLIC_URL) as script:
        assert script.get("/api/me", headers={"Authorization": f"Bearer {made.json()['secret']}"}).status_code == 200


def test_changes_must_come_from_a_page_of_pinax(browser, provider):
    sign_in(browser, provider, preferred_username="ivy@example.org")
    elsewhere = browser.post("/api/tokens", json={"name": "Report", "days": 30}, headers={"Origin": "https://evil.example"})
    assert elsewhere.status_code == 403


def test_the_access_review_knows_who_signed_in(browser, provider):
    sign_in(browser, provider, preferred_username="ivy@example.org")
    assert browser.get("/api/admin/access-review").json()["local"] is True  # Pinax sees each sign-in


def test_the_sign_in_pages_are_there_only_in_oidc_mode(client):
    assert client.get("/auth/login", follow_redirects=False).status_code == 404
    assert client.get("/auth/callback", follow_redirects=False).status_code == 404


def test_missing_settings_stop_pinax(catalog, monkeypatch):
    from pinax.main import app

    assert oidc.problems(Settings()) == []  # the other modes need none of them
    found = oidc.problems(Settings(auth_mode="oidc", session_secret="short", public_url="http://pinax.example"))
    assert "PINAX_OIDC_ISSUER is not set" in found and "PINAX_OIDC_CLIENT_SECRET is not set" in found
    assert any("at least 32 characters" in p for p in found) and any("https://" in p for p in found)
    assert oidc.problems(Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_client_id="c", oidc_client_secret="s",
                                  public_url="http://localhost:8001", session_secret="x" * 32)) == []
    monkeypatch.setenv("PINAX_AUTH_MODE", "oidc")
    config.get_settings.cache_clear()
    assert "PINAX_OIDC_ISSUER is not set" in config.unsafe(config.get_settings())
    with pytest.raises(RuntimeError, match="PINAX_OIDC_ISSUER is not set"), TestClient(app):
        pass
