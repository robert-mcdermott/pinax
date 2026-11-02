# SPDX-License-Identifier: Apache-2.0
"""Sign-in with Microsoft Entra ID, or any OpenID Connect provider, in Pinax itself (PINAX_AUTH_MODE=oidc).

Someone without a session goes to /auth/login, which sends the browser to the provider. The provider sends it back
to /auth/callback with a one-time code, which Pinax exchanges for a signed ID token: Authlib checks its signature
against the provider's published keys, and its issuer, audience, expiry, and nonce, and PKCE ties the code to this
browser. The person is then matched to Pinax's people by one claim, never made, and gets a server-side session, as
local sign-in gives (signin.py), so signing out and deactivation work the same. Only the sign-in's own state (its
`state`, nonce, and PKCE verifier) is kept in a signed cookie, for the minutes a sign-in takes. /auth/logout ends
Pinax's session and the provider's. Following Tundra's.

/auth/emergency (A12) is a password form for emergency accounts, storage administrators marked from the command line
(`pinax user emergency`), for when the provider fails. It answers 404 unless one exists; the sign-in itself is
signin.sign_in(emergency=True).
"""

import html
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth, OAuthError, StarletteOAuth2App
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from joserfc.errors import JoseError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import logs, people, signin
from .config import Settings, get_settings
from .db import get_session, utcnow
from .models import User

STATE_COOKIE = "pinax_signing_in"  # the sign-in's state only; Pinax's session is signin.COOKIE
STATE_MINUTES = 10
SCOPES = "openid profile email"
SESSION_SECRET_LENGTH = 32
HTTP_TRANSPORT = None  # the tests route Pinax's calls to the provider to a stand-in
_clients: dict[tuple, StarletteOAuth2App] = {}

router = APIRouter(prefix="/auth", include_in_schema=False)


def problems(settings: Settings) -> list[str]:
    """What stops sign-in with an identity provider from working, checked when Pinax starts."""
    if settings.auth_mode != "oidc":
        return []
    found = [f"PINAX_{name.upper()} is not set" for name in
             ("oidc_issuer", "oidc_client_id", "oidc_client_secret", "public_url", "session_secret")
             if not _value(getattr(settings, name))]
    secret = _value(settings.session_secret)
    if secret and len(secret) < SESSION_SECRET_LENGTH:
        found.append(f"PINAX_SESSION_SECRET must be at least {SESSION_SECRET_LENGTH} characters "
                     "(`openssl rand -hex 32` makes one)")
    local = ("http://localhost", "http://127.0.0.1")
    if settings.public_url and not settings.public_url.startswith(("https://", *local)):
        found.append("PINAX_PUBLIC_URL must start with https:// (http:// only for localhost)")
    for (role, division), values in settings.mapped_roles.items():
        if role not in ("admin", "auditor", "finance", "division") or bool(division) != (role == "division"):
            found.append(f"PINAX_OIDC_ROLE_MAP: {role + (':' + division if division else '')!r} is not a role: use admin, "
                         "auditor, finance, or division:<Division>")
        elif not values or not all(isinstance(v, str) and v for v in values):
            found.append(f"PINAX_OIDC_ROLE_MAP: {role + (':' + division if division else '')!r} needs the values that give it")
    return found


def _value(setting) -> str:
    return setting.get_secret_value() if hasattr(setting, "get_secret_value") else (setting or "")


def identity_from_claims(claims: dict, settings: Settings) -> tuple[str, str] | None:
    """The username a signed-in identity may be in Pinax, in its two forms (whole, and without its domain), or None.

    Without PINAX_OIDC_USERNAME_DOMAINS, the claim must be a person's username exactly, such as the user principal
    name `jdoe@example.org`. With them, it must be in one of those domains, and matches either `jdoe@example.org`
    or `jdoe`; any other domain is refused, so a guest `jdoe@example.com` never signs in as staff `jdoe`.
    """
    value = str(claims.get(settings.oidc_username_claim) or "").strip().lower()
    domains = settings.oidc_domains
    if not domains:
        return (value, value) if value else None
    local, _, domain = value.rpartition("@")
    return (value, local) if local and domain in domains else None


def find_person(session: Session, forms: tuple[str, str]) -> User | None:
    whole, short = forms
    people = session.scalars(select(User).where(func.lower(User.username).in_({whole, short}))).all()
    return next((u for u in people if u.username.lower() == whole), people[0] if people else None)


def client(settings: Settings) -> StarletteOAuth2App:
    key = (settings.oidc_issuer, settings.oidc_client_id, _value(settings.oidc_client_secret), id(HTTP_TRANSPORT))
    if key not in _clients:
        kwargs = {"scope": SCOPES, "code_challenge_method": "S256"}
        if HTTP_TRANSPORT is not None:
            kwargs["transport"] = HTTP_TRANSPORT
        oauth = OAuth()
        oauth.register("idp", client_id=settings.oidc_client_id, client_secret=_value(settings.oidc_client_secret),
                       server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                       client_kwargs=kwargs)
        _clients[key] = oauth.idp
    return _clients[key]


class SignInState:
    """Starlette's signed-cookie sessions, for the sign-in's state, on /auth/ only, in oidc mode only."""

    def __init__(self, app):
        self.app = app
        self._built: dict[tuple, SessionMiddleware] = {}

    async def __call__(self, scope, receive, send):
        settings = get_settings()
        secret = _value(settings.session_secret)
        if (scope["type"] != "http" or settings.auth_mode != "oidc" or not secret
                or not scope["path"].startswith("/auth/")):
            return await self.app(scope, receive, send)
        key = (secret, settings.public_url)
        if key not in self._built:
            self._built[key] = SessionMiddleware(
                self.app, secret_key=secret, session_cookie=STATE_COOKIE, path="/auth/",
                max_age=STATE_MINUTES * 60, same_site="lax", https_only=settings.public_url.startswith("https://"))
        return await self._built[key](scope, receive, send)


def _oidc_settings() -> Settings:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        raise HTTPException(404)
    return settings


def _callback_url(settings: Settings) -> str:
    return settings.public_url.rstrip("/") + "/auth/callback"


def _safe_next(target: str) -> str:
    """Only pages of Pinax, so the sign-in link cannot send people elsewhere."""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/app/"


def _address(request: Request) -> str:
    info = logs.current.get()
    return info.address if info else (request.client.host if request.client else "")


@router.get("/login")
async def login(request: Request, settings: Settings = Depends(_oidc_settings), next: str = "/app/"):
    request.session.clear()
    request.session["next"] = _safe_next(next)
    return await client(settings).authorize_redirect(request, _callback_url(settings))


@router.get("/callback")
async def callback(request: Request, settings: Settings = Depends(_oidc_settings),
                   session: Session = Depends(get_session)):
    if request.query_params.get("error"):  # for example, the person is not assigned to the application
        detail = request.query_params.get("error_description") or request.query_params["error"]
        return _page("Sign-in did not complete", f"The sign-in service said: {detail}", TRY_AGAIN, status=403)
    try:
        token = await client(settings).authorize_access_token(request)
    except (OAuthError, JoseError) as exc:  # a wrong state, a used code, or a token that fails its checks
        reason = getattr(exc, "description", "") or getattr(exc, "error", "") or type(exc).__name__
        return _page("Sign-in did not complete", f"Pinax could not confirm the sign-in ({reason}). Try again; if it "
                     "keeps happening, tell your Pinax administrators.", TRY_AGAIN, status=403)
    except httpx2.HTTPError as exc:
        return _page("Sign-in did not complete", f"Pinax could not reach the sign-in service ({exc}). Try again "
                     "later; if it keeps happening, tell your Pinax administrators.", TRY_AGAIN, status=502)
    claims = token.get("userinfo") or {}
    shown = str(claims.get(settings.oidc_username_claim) or claims.get("email") or claims.get("sub")
                or "an unknown account")[:200]
    forms = identity_from_claims(claims, settings)
    user = find_person(session, forms) if forms else None
    address = _address(request)
    if user is None or not user.active:
        reason = "deactivated" if user else "no Pinax account" if forms else "not an allowed domain"
        signin.audit(session, "auth.sign_in_refused", f"Sign-in refused for {shown}: {reason}", user,
                     username=None if user else shown, identity=shown, reason=reason, method="oidc", address=address)
        session.commit()
        ask = "Ask your Pinax administrators to add you, with your lab or department, under People and access."
        why = ("Your Pinax account has been deactivated. Ask your Pinax administrators if you need it again." if user
               else f"Pinax has no account for you yet. {ask}" if forms
               else "Your account is not one Pinax accepts here. Sign in with your organization's account.")
        return _page("You can't use Pinax yet", f"You signed in as {shown}. {why}",
                     [("/auth/logout", "Sign in as someone else")], status=403)
    target = request.session.get("next", "/app/")
    request.session.clear()  # the sign-in's state is spent
    if settings.mapped_roles:
        people.roles_from_provider(session, user, claims, settings)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, utcnow()
    cookie = signin.start_session(session, user, address)
    signin.audit(session, "auth.signed_in", f"{user.display_name} signed in", user, identity=shown, method="oidc",
                 address=address)
    session.commit()
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(signin.COOKIE, cookie, httponly=True, samesite="lax", secure=settings.secure_cookies,
                        max_age=settings.session_hours * 3600, path="/")
    return response


@router.get("/logout")
async def logout(request: Request, settings: Settings = Depends(_oidc_settings),
                 session: Session = Depends(get_session)):
    request.session.clear()
    ended = signin.end_session(session, request.cookies.get(signin.COOKIE))
    if ended is not None:
        signin.audit(session, "auth.signed_out", f"{ended.user.display_name} signed out", ended.user, method="oidc",
                     address=_address(request))
    session.commit()
    signed_out = settings.public_url.rstrip("/") + "/auth/signed-out"
    try:
        end_session = (await client(settings).load_server_metadata()).get("end_session_endpoint")
    except Exception:  # noqa: BLE001 - signing out of Pinax works even if the provider cannot be reached
        end_session = None
    target = f"{end_session}?{urlencode({'post_logout_redirect_uri': signed_out, 'client_id': settings.oidc_client_id})}" \
        if end_session else signed_out
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(signin.COOKIE, path="/")
    return response


@router.get("/signed-out")
def signed_out(settings: Settings = Depends(_oidc_settings)):
    return _page("You have signed out", "You have signed out of Pinax.", [("/auth/login", "Sign in again")])


def _emergency_accounts(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.emergency, User.is_admin, User.active))


@router.get("/emergency")
def emergency_page(settings: Settings = Depends(_oidc_settings), session: Session = Depends(get_session)):
    if not _emergency_accounts(session):
        raise HTTPException(404)
    return _emergency_form()


@router.post("/emergency")
async def emergency_sign_in(request: Request, settings: Settings = Depends(_oidc_settings),
                            session: Session = Depends(get_session)):
    if not _emergency_accounts(session):
        raise HTTPException(404)
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        return _emergency_form("Refused: the request did not come from this page.", status=403)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413)
    form = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}
    try:  # scrypt takes a moment: off the event loop
        _, token = await run_in_threadpool(signin.sign_in, session, form.get("username", ""), form.get("password", ""),
                                           _address(request), emergency=True)
    except signin.SignInError as e:
        return _emergency_form(str(e), status=e.status)
    response = RedirectResponse("/app/", status_code=303)
    response.set_cookie(signin.COOKIE, token, httponly=True, samesite="lax", secure=settings.secure_cookies,
                        max_age=signin.EMERGENCY_SESSION_HOURS * 3600, path="/")
    return response


def _emergency_form(problem: str = "", status: int = 200) -> HTMLResponse:
    form = f"""{f'<div class="alert alert-reject" role="alert">{html.escape(problem)}</div>' if problem else ""}
<form method="post" action="/auth/emergency" style="margin-top:12px">
<div class="field"><label for="username">Username</label>
<input class="input" id="username" name="username" autocomplete="username" required></div>
<div class="field"><label for="password">Password</label>
<input class="input" id="password" name="password" type="password" autocomplete="current-password" required></div>
<p><button class="btn btn-primary" type="submit">Sign in</button></p></form>"""
    return _page("Emergency sign-in", "For a storage administrator's emergency account, when signing in with your "
                 "organization's account is not working. Each sign-in lasts an hour, and every use is recorded and "
                 "reported to the site's monitoring.", [], status=status, extra=form)


TRY_AGAIN = [("/auth/login", "Try again")]


def _page(title: str, message: str, actions: list[tuple[str, str]], status: int = 200,
          extra: str = "") -> HTMLResponse:
    """A sign-in page in Pinax's look: the top bar, a message, and what to do next (`extra`: markup Pinax wrote)."""
    buttons = " ".join(f'<a class="btn {"btn-primary" if i == 0 else "btn-secondary"}" href="{href}">'
                       f'{html.escape(label)}</a>' for i, (href, label) in enumerate(actions))
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Pinax</title><link rel="icon" href="/img/favicon.svg" type="image/svg+xml">
<script>try{{if(localStorage.getItem("pinax-theme")==="dark")document.documentElement.dataset.theme="dark"}}catch(e){{}}</script>
<link rel="stylesheet" href="/css/pinax.css">
</head><body>
<header class="topbar"><a class="brand" href="/" aria-label="Pinax home"><img class="brand-mark" src="/img/pinax-mark.svg" alt="">
<span class="signpost"><span class="signpost-name">Pinax</span><span class="signpost-sub">Explore your data</span></span></a></header>
<main style="max-width:640px;margin:48px auto;padding:0 16px"><div class="card">
<h1 style="margin-top:0">{html.escape(title)}</h1><p>{html.escape(message)}</p>{extra}{f"<p>{buttons}</p>" if buttons else ""}
</div></main></body></html>"""
    return HTMLResponse(body, status_code=status)
