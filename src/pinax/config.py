# SPDX-License-Identifier: Apache-2.0
"""Runtime settings, read from PINAX_* environment variables or a .env file."""

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PINAX_", env_file=".env", extra="ignore")

    app_name: str = "Pinax"
    environment: Literal["demo", "dev", "prod"] = "demo"

    # Where things live. VAR_ROOT holds the catalog database and the snapshot indexes built from scans;
    # SCAN_ROOT is where `pinax scan` writes fs-inventory output (scans made elsewhere can live anywhere).
    var_root: Path = PROJECT_ROOT / "var"
    scan_root: Path = PROJECT_ROOT / "scans"
    demo_root: Path = PROJECT_ROOT / "demo"
    # The catalog: SQLite in VAR_ROOT by default, or PostgreSQL (`postgresql://user@host/db`, with the `postgres`
    # extra installed) for production. Migrations bring it up to date at start-up, unless DB_AUTO_UPGRADE is false
    # and `pinax db upgrade` is left to whoever runs the database. DATABASE_POOL_SIZE: PostgreSQL connections kept
    # open per process, and as many more at busy times.
    database_url: str = ""
    db_auto_upgrade: bool = True
    database_pool_size: int = Field(10, ge=1, le=100)
    # Threads that run the web app's requests (each request's sign-in check and page, one after the other).
    web_threads: int = Field(40, ge=2, le=200)

    # fs-inventory. Defaults to bin/fs-inventory (scripts/install-fs-inventory.sh), then the PATH.
    fs_inventory: str = ""
    scan_threads: int = 32
    scan_extra_args: list[str] = []

    # Building snapshot indexes. DuckDB uses this much memory and these threads, spilling beside the index
    # beyond the memory limit. 0 threads means one per CPU; steps that read the scan use fewer if the memory
    # limit requires. More memory doesn't make the build faster (measured at 100 million files).
    build_memory_limit: str = "8GB"
    build_threads: int = 0
    # Snapshots kept per filesystem; older indexes are deleted, their folder history is kept.
    keep_snapshots: int = 6

    # Query side. A request's queries stop after QUERY_TIMEOUT seconds (0: no limit), or when the browser leaves;
    # keep it below the proxy's timeout, so people see why. At most WIDE_QUERIES file-level queries of a whole
    # filesystem (searches and comparisons that read every file) run at once (0: no limit). QUERY_MEMORY_LIMIT is
    # for every open index together, each with an equal share (snapshots.IndexCache).
    query_memory_limit: str = Field("8GB", pattern=r"(?i)^\s*\d+(\.\d+)?\s*([KMGT]i?B|B)\s*$")
    query_threads: int = 0
    query_timeout: int = 180
    wide_queries: int = 2
    search_max_rows: int = 500
    export_max_rows: int = 100_000

    # Reporting defaults. Data counts as cold when nothing in it has changed for this many days.
    cold_days: int = 730
    # Costs: the currency prices are in (an ISO 4217 code), shown in the viewer's own number format.
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    # Searches run in the background (jobs.py): how long one may run, in seconds, and how many days its results are
    # kept. They take the same turns as whole-filesystem queries (wide_queries).
    job_timeout: int = Field(3600, ge=60)
    job_days: int = Field(7, ge=1, le=365)
    # Capacity: a filesystem that fills within this many months at its trend is flagged on the overview.
    fill_warning_months: int = Field(12, ge=1, le=120)

    # Logs (logs.py): text for the journal or JSON lines for a log collector, to standard error or LOG_FILE (which
    # `logrotate` can move; Pinax reopens it). One line per request to the API unless LOG_REQUESTS is false; a
    # request slower than SLOW_REQUEST_SECONDS is a warning (0: never). Audit events are logged too, and also
    # written to AUDIT_LOG, as JSON lines, if it is set.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: Path | None = None
    log_requests: bool = True
    slow_request_seconds: float = Field(5.0, ge=0)
    audit_log: Path | None = None
    # Metrics (metrics.py): /metrics in Prometheus's format, only for requests with this token
    # (`Authorization: Bearer ...`); unset, /metrics is off.
    metrics_token: SecretStr | None = None

    # Identity. "local": people sign in with a password Pinax keeps, the default outside the demo, with nothing else
    # to set up. "demo" uses a persona cookie: anyone picks anyone, for showing Pinax with the seeded people, the
    # default in the demo environment (refused when ENVIRONMENT=prod). "header" trusts a username header
    # (X-Remote-User by default) set by a single sign-on proxy, and only on requests from TRUSTED_PROXIES (addresses
    # or networks; a JSON list). "oidc": Pinax signs people in with Microsoft Entra ID, or another OpenID Connect
    # provider, itself (oidc.py).
    auth_mode: Literal["demo", "local", "header", "oidc"] | None = None
    auth_header: str = "X-Remote-User"
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]
    # Local sign-in. A session lasts this long from signing in. This many failed sign-ins in a row lock the account
    # for this many minutes. Passwords need at least this many characters. Session cookies are marked Secure (sent
    # only over HTTPS) when COOKIE_SECURE is true, or by default when ENVIRONMENT=prod.
    session_hours: int = 12
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    password_min_length: int = 12
    cookie_secure: bool | None = None
    # API tokens (tokens.py) expire after at most this many days.
    token_max_days: int = 365
    # Sign-in with an identity provider (oidc). OIDC_ISSUER for Entra ID: https://login.microsoftonline.com/<tenant
    # ID>/v2.0. The username is OIDC_USERNAME_CLAIM (Entra ID's user principal name by default); with
    # OIDC_USERNAME_DOMAINS (comma-separated), only those domains, and `jdoe@example.org` may be `jdoe` in Pinax.
    # PUBLIC_URL is how people reach Pinax; SESSION_SECRET signs the sign-in's state (at least 32 characters).
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr | None = None
    oidc_username_claim: str = "preferred_username"
    oidc_username_domains: str = ""
    public_url: str = ""
    session_secret: SecretStr | None = None
    # Roles from the identity provider (oidc), optional. OIDC_ROLE_MAP, as JSON, maps Pinax's roles ("admin",
    # "auditor", "finance", and "division:<Division>") to the values of OIDC_ROLES_CLAIM ("roles": Entra ID's app
    # roles; or "groups": group IDs) that give them. A role it names is set at every sign-in, and only there.
    oidc_roles_claim: Literal["roles", "groups"] = "roles"
    oidc_role_map: dict[str, list[str] | str] = {}

    @model_validator(mode="after")
    def _default_sign_in(self) -> "Settings":
        if self.auth_mode is None:
            self.auth_mode = "demo" if self.environment == "demo" else "local"
        return self

    @property
    def mapped_roles(self) -> dict[tuple[str, str], set[str]]:
        """(role, division), "admin" among them, to the claim values that give it; only with sign-in by a provider."""
        if self.auth_mode != "oidc":
            return {}
        out: dict[tuple[str, str], set[str]] = {}
        for key, values in self.oidc_role_map.items():
            role, _, division = key.partition(":")
            out[(role.strip(), division.strip())] = {values} if isinstance(values, str) else set(values)
        return out

    @property
    def oidc_domains(self) -> list[str]:
        return [d.strip().lower().lstrip("@") for d in self.oidc_username_domains.split(",") if d.strip()]

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "prod" if self.cookie_secure is None else self.cookie_secure

    @property
    def db_url(self) -> str:
        url = self.database_url or f"sqlite:///{self.var_root / 'pinax.db'}"
        for plain in ("postgresql://", "postgres://"):  # PostgreSQL's own form of URL, with Pinax's driver
            if url.startswith(plain):
                return "postgresql+psycopg://" + url.removeprefix(plain)
        return url

    @property
    def snapshot_root(self) -> Path:
        return self.var_root / "snapshots"

    def fs_inventory_path(self) -> str:
        if self.fs_inventory:
            return self.fs_inventory
        local = PROJECT_ROOT / "bin" / "fs-inventory"
        if local.exists():
            return str(local)
        return shutil.which("fs-inventory") or "fs-inventory"

    def ensure_dirs(self) -> None:
        for p in (self.var_root, self.snapshot_root):
            p.mkdir(parents=True, exist_ok=True)


METRICS_TOKEN_LENGTH = 16


def unsafe(settings: Settings) -> str | None:
    """Why these settings must not run, or None."""
    if settings.environment == "prod" and settings.auth_mode == "demo":
        return ("PINAX_AUTH_MODE=demo lets anyone sign in as anyone, so it is refused when PINAX_ENVIRONMENT=prod. "
                "Use local (passwords Pinax keeps), oidc (Entra ID), or header (a single sign-on proxy), or a demo or "
                "dev environment.")
    from .oidc import problems

    if found := problems(settings):
        return "Sign-in with an identity provider (PINAX_AUTH_MODE=oidc) needs its settings: " + "; ".join(found) + "."
    token = settings.metrics_token.get_secret_value() if settings.metrics_token else ""
    if token and len(token) < METRICS_TOKEN_LENGTH:
        return (f"PINAX_METRICS_TOKEN must be at least {METRICS_TOKEN_LENGTH} characters, since it is all that guards "
                "/metrics: `openssl rand -hex 32` makes one.")
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
