"""Environment and token-file configuration."""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_API_BASE_URL = "https://www.patreon.com/api/oauth2/v2"
DEFAULT_TOKEN_URL = "https://www.patreon.com/api/oauth2/token"  # noqa: S105  # nosec B105
DEFAULT_WEB_BASE_URL = "https://www.patreon.com"
MAX_TIMEOUT_SECONDS = 300.0
MAX_TOKEN_FILE_BYTES = 1_000_000
MAX_COOKIE_FILE_BYTES = 65_536


class ConfigError(Exception):
    """Raised when the server cannot be configured safely."""


@dataclass(frozen=True)
class Settings:
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    token_file: Path | None = None
    session_cookie: str | None = field(default=None, repr=False)
    session_cookie_file: Path | None = None
    web_user_agent: str | None = None
    webhook_allowed_hosts: tuple[str, ...] = ()
    allow_pii: bool = False
    api_base_url: str = DEFAULT_API_BASE_URL
    token_url: str = DEFAULT_TOKEN_URL
    web_base_url: str = DEFAULT_WEB_BASE_URL
    timeout_seconds: float = 30.0
    expires_at: float | None = None

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    @property
    def has_session(self) -> bool:
        return bool(self.session_cookie)

    def require_auth(self) -> Settings:
        if not self.access_token and not self.can_refresh and not self.has_session:
            raise ConfigError(
                "No Patreon credentials found. Set PATREON_ACCESS_TOKEN, or set "
                "PATREON_REFRESH_TOKEN, PATREON_CLIENT_ID, and PATREON_CLIENT_SECRET "
                "so the server can obtain one. To use the web post endpoints, set "
                "PATREON_SESSION_COOKIE or PATREON_SESSION_COOKIE_FILE."
            )
        return self

    def with_tokens(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ) -> Settings:
        return replace(
            self,
            access_token=access_token or self.access_token,
            refresh_token=refresh_token or self.refresh_token,
            expires_at=expires_at,
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _validate_base_url(value: str, name: str) -> str:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ConfigError(f"{name} is not a valid URL: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ConfigError(f"{name} must be an absolute http(s) URL.")
    if parsed.username or parsed.password:
        raise ConfigError(f"{name} must not contain embedded credentials.")
    loopback = {"127.0.0.1", "localhost", "::1"}
    if hostname not in loopback and hostname != "www.patreon.com":
        raise ConfigError(f"{name} must point at www.patreon.com or loopback for local tests.")
    if parsed.scheme == "http" and hostname not in loopback:
        raise ConfigError(f"{name} must use https unless it points at loopback.")
    return value.rstrip("/")


def _read_secret_text(
    path: Path,
    label: str,
    max_bytes: int,
    *,
    missing_ok: bool,
) -> str | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ConfigError(f"{label} at {path} does not exist.") from None
    except OSError as exc:
        raise ConfigError(f"Cannot inspect {label} at {path}: {exc}") from exc

    if stat.S_ISLNK(info.st_mode):
        raise ConfigError(f"{label} at {path} must not be a symlink.")
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"{label} at {path} must be a regular file.")
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and info.st_uid != geteuid():
        raise ConfigError(f"{label} at {path} must be owned by the current user.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigError(
            f"{label} at {path} must not be readable or writable by group/others; run chmod 600."
        )
    if info.st_size > max_bytes:
        raise ConfigError(f"{label} at {path} is too large.")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ConfigError(f"Cannot read {label} at {path}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise ConfigError(f"{label} at {path} must be a regular file.")
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and opened.st_uid != geteuid():
            raise ConfigError(f"{label} at {path} must be owned by the current user.")
        if stat.S_IMODE(opened.st_mode) & 0o077:
            raise ConfigError(
                f"{label} at {path} must not be readable or writable by "
                "group/others; run chmod 600."
            )
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(max_bytes + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > max_bytes:
        raise ConfigError(f"{label} at {path} is too large.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{label} at {path} is not valid UTF-8.") from exc


def _read_token_file(path: Path) -> dict[str, Any]:
    text = _read_secret_text(
        path,
        "PATREON_TOKEN_FILE",
        MAX_TOKEN_FILE_BYTES,
        missing_ok=True,
    )
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"PATREON_TOKEN_FILE at {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"PATREON_TOKEN_FILE at {path} must contain a JSON object.")
    return payload


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from environment variables, with an optional token file.

    Environment values win over file values. The token file is only read when
    ``PATREON_TOKEN_FILE`` points at it, so the server never persists credentials
    unless the operator asks it to.
    """

    env = os.environ if env is None else env
    env_access = _clean(env.get("PATREON_ACCESS_TOKEN"))
    env_refresh = _clean(env.get("PATREON_REFRESH_TOKEN"))

    token_file_value = _clean(env.get("PATREON_TOKEN_FILE"))
    token_file = Path(token_file_value).expanduser() if token_file_value else None
    stored = _read_token_file(token_file) if token_file and not env_access else {}

    stored_access = _clean(stored.get("access_token"))
    stored_refresh = _clean(stored.get("refresh_token"))
    stored_expires = stored.get("expires_at")
    expires_at: float | None
    if env_access:
        # An env access token wins outright; a stale file expiry must not force
        # a refresh with a possibly rotated refresh token.
        expires_at = None
    else:
        try:
            expires_at = float(stored_expires) if stored_expires is not None else None
        except (TypeError, ValueError):
            expires_at = None

    timeout_raw = _clean(env.get("PATREON_TIMEOUT_SECONDS")) or "30"
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ConfigError("PATREON_TIMEOUT_SECONDS must be a number.") from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ConfigError("PATREON_TIMEOUT_SECONDS must be a positive finite number.")
    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ConfigError(f"PATREON_TIMEOUT_SECONDS must be at most {MAX_TIMEOUT_SECONDS:g}.")

    env_cookie = _clean(env.get("PATREON_SESSION_COOKIE"))
    cookie_file_value = _clean(env.get("PATREON_SESSION_COOKIE_FILE"))
    session_cookie_file = Path(cookie_file_value).expanduser() if cookie_file_value else None
    file_cookie: str | None = None
    if session_cookie_file is not None and not env_cookie:
        file_cookie = _clean(
            _read_secret_text(
                session_cookie_file,
                "PATREON_SESSION_COOKIE_FILE",
                MAX_COOKIE_FILE_BYTES,
                missing_ok=False,
            )
        )
        if not file_cookie:
            raise ConfigError(f"PATREON_SESSION_COOKIE_FILE at {session_cookie_file} is empty.")

    allowed_hosts_raw = _clean(env.get("PATREON_WEBHOOK_ALLOWED_HOSTS"))
    allowed_hosts = (
        tuple(host.strip().lower() for host in allowed_hosts_raw.split(",") if host.strip())
        if allowed_hosts_raw
        else ()
    )
    for host in allowed_hosts:
        try:
            parsed_host = urlparse(f"//{host}").hostname
        except ValueError:
            parsed_host = None
        if parsed_host != host:
            raise ConfigError(
                "PATREON_WEBHOOK_ALLOWED_HOSTS must be comma-separated hostnames "
                "without schemes or ports."
            )

    api_base_url = _validate_base_url(
        _clean(env.get("PATREON_API_BASE_URL")) or DEFAULT_API_BASE_URL,
        "PATREON_API_BASE_URL",
    )
    token_url = _validate_base_url(
        _clean(env.get("PATREON_TOKEN_URL")) or DEFAULT_TOKEN_URL,
        "PATREON_TOKEN_URL",
    )
    web_base_url = _validate_base_url(
        _clean(env.get("PATREON_WEB_BASE_URL")) or DEFAULT_WEB_BASE_URL,
        "PATREON_WEB_BASE_URL",
    )

    settings = Settings(
        access_token=env_access or stored_access,
        refresh_token=env_refresh or (stored_refresh if not env_access else None),
        client_id=_clean(env.get("PATREON_CLIENT_ID")),
        client_secret=_clean(env.get("PATREON_CLIENT_SECRET")),
        token_file=token_file,
        session_cookie=env_cookie or file_cookie,
        session_cookie_file=session_cookie_file,
        web_user_agent=_clean(env.get("PATREON_USER_AGENT")),
        webhook_allowed_hosts=allowed_hosts,
        allow_pii=(_clean(env.get("PATREON_ALLOW_PII")) or "").lower()
        in {"1", "true", "yes", "on"},
        api_base_url=api_base_url,
        token_url=token_url,
        web_base_url=web_base_url,
        timeout_seconds=timeout_seconds,
        expires_at=expires_at,
    )
    return settings
