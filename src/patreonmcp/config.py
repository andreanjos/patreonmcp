"""Environment and token-file configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_API_BASE_URL = "https://www.patreon.com/api/oauth2/v2"
DEFAULT_TOKEN_URL = "https://www.patreon.com/api/oauth2/token"
DEFAULT_WEB_BASE_URL = "https://www.patreon.com"


class ConfigError(Exception):
    """Raised when the server cannot be configured safely."""


@dataclass(frozen=True)
class Settings:
    access_token: str | None = None
    refresh_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    token_file: Path | None = None
    session_cookie: str | None = None
    session_cookie_file: Path | None = None
    web_user_agent: str | None = None
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
            expires_at=expires_at if expires_at is not None else self.expires_at,
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _read_token_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read PATREON_TOKEN_FILE at {path}: {exc}") from exc
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
    token_file_value = _clean(env.get("PATREON_TOKEN_FILE"))
    token_file = Path(token_file_value).expanduser() if token_file_value else None
    stored = _read_token_file(token_file) if token_file else {}

    stored_access = _clean(stored.get("access_token"))
    stored_refresh = _clean(stored.get("refresh_token"))
    stored_expires = stored.get("expires_at")
    expires_at: float | None
    try:
        expires_at = float(stored_expires) if stored_expires is not None else None
    except (TypeError, ValueError):
        expires_at = None

    timeout_raw = _clean(env.get("PATREON_TIMEOUT_SECONDS")) or "30"
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ConfigError("PATREON_TIMEOUT_SECONDS must be a number.") from exc
    if timeout_seconds <= 0:
        raise ConfigError("PATREON_TIMEOUT_SECONDS must be greater than zero.")

    cookie_file_value = _clean(env.get("PATREON_SESSION_COOKIE_FILE"))
    session_cookie_file = Path(cookie_file_value).expanduser() if cookie_file_value else None
    file_cookie: str | None = None
    if session_cookie_file is not None:
        try:
            file_cookie = _clean(session_cookie_file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(
                f"Cannot read PATREON_SESSION_COOKIE_FILE at {session_cookie_file}: {exc}"
            ) from exc
        if not file_cookie:
            raise ConfigError(f"PATREON_SESSION_COOKIE_FILE at {session_cookie_file} is empty.")

    settings = Settings(
        access_token=_clean(env.get("PATREON_ACCESS_TOKEN")) or stored_access,
        refresh_token=_clean(env.get("PATREON_REFRESH_TOKEN")) or stored_refresh,
        client_id=_clean(env.get("PATREON_CLIENT_ID")),
        client_secret=_clean(env.get("PATREON_CLIENT_SECRET")),
        token_file=token_file,
        session_cookie=(_clean(env.get("PATREON_SESSION_COOKIE")) or file_cookie),
        session_cookie_file=session_cookie_file,
        web_user_agent=_clean(env.get("PATREON_USER_AGENT")),
        api_base_url=(_clean(env.get("PATREON_API_BASE_URL")) or DEFAULT_API_BASE_URL).rstrip("/"),
        token_url=_clean(env.get("PATREON_TOKEN_URL")) or DEFAULT_TOKEN_URL,
        web_base_url=(_clean(env.get("PATREON_WEB_BASE_URL")) or DEFAULT_WEB_BASE_URL).rstrip("/"),
        timeout_seconds=timeout_seconds,
        expires_at=expires_at,
    )
    return settings
