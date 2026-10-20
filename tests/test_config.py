from __future__ import annotations

import json

import pytest

from patreonmcp.config import ConfigError, load_settings


def test_env_values_override_token_file(tmp_path) -> None:
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        json.dumps(
            {
                "access_token": "file-token",
                "refresh_token": "file-refresh",
                "expires_at": 123,
            }
        )
    )

    settings = load_settings(
        {
            "PATREON_TOKEN_FILE": str(token_file),
            "PATREON_ACCESS_TOKEN": "env-token",
        }
    )

    assert settings.access_token == "env-token"
    assert settings.refresh_token == "file-refresh"
    assert settings.expires_at == 123.0


def test_missing_credentials_are_rejected() -> None:
    with pytest.raises(ConfigError, match="No Patreon credentials"):
        load_settings({}).require_auth()


def test_refresh_credentials_satisfy_auth() -> None:
    settings = load_settings(
        {
            "PATREON_REFRESH_TOKEN": "r",
            "PATREON_CLIENT_ID": "i",
            "PATREON_CLIENT_SECRET": "s",
        }
    )
    assert settings.require_auth() is settings
    assert settings.can_refresh


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ConfigError, match="PATREON_TIMEOUT_SECONDS"):
        load_settings({"PATREON_ACCESS_TOKEN": "x", "PATREON_TIMEOUT_SECONDS": "nope"})


def test_missing_token_file_is_treated_as_empty(tmp_path) -> None:
    settings = load_settings(
        {
            "PATREON_TOKEN_FILE": str(tmp_path / "missing.json"),
            "PATREON_ACCESS_TOKEN": "x",
        }
    )
    assert settings.access_token == "x"


def test_session_cookie_file_is_loaded(tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("session_id=abc; a_csrf=def\n")

    settings = load_settings({"PATREON_SESSION_COOKIE_FILE": str(cookie_file)})

    assert settings.session_cookie == "session_id=abc; a_csrf=def"
    assert settings.session_cookie_file == cookie_file
    assert settings.require_auth() is settings


def test_env_session_cookie_overrides_file(tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("session_id=file\n")

    settings = load_settings(
        {
            "PATREON_SESSION_COOKIE_FILE": str(cookie_file),
            "PATREON_SESSION_COOKIE": "session_id=env",
        }
    )

    assert settings.session_cookie == "session_id=env"


def test_missing_session_cookie_file_is_an_error(tmp_path) -> None:
    with pytest.raises(ConfigError, match="PATREON_SESSION_COOKIE_FILE"):
        load_settings({"PATREON_SESSION_COOKIE_FILE": str(tmp_path / "missing-cookies.txt")})
