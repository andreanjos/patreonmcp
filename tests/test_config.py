from __future__ import annotations

import json

import pytest

from patreonmcp.config import ConfigError, Settings, load_settings


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
    token_file.chmod(0o600)

    settings = load_settings(
        {
            "PATREON_TOKEN_FILE": str(token_file),
            "PATREON_ACCESS_TOKEN": "env-token",
        }
    )

    assert settings.access_token == "env-token"
    assert settings.refresh_token is None
    assert settings.expires_at is None


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
    cookie_file.chmod(0o600)

    settings = load_settings({"PATREON_SESSION_COOKIE_FILE": str(cookie_file)})

    assert settings.session_cookie == "session_id=abc; a_csrf=def"
    assert settings.session_cookie_file == cookie_file
    assert settings.require_auth() is settings


def test_env_session_cookie_overrides_file(tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("session_id=file\n")
    cookie_file.chmod(0o600)

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


def test_public_http_base_urls_are_rejected() -> None:
    with pytest.raises(ConfigError, match="must use https"):
        load_settings(
            {"PATREON_ACCESS_TOKEN": "x", "PATREON_API_BASE_URL": "http://www.patreon.com/api"}
        )


def test_loopback_http_base_urls_are_allowed() -> None:
    settings = load_settings(
        {"PATREON_ACCESS_TOKEN": "x", "PATREON_API_BASE_URL": "http://127.0.0.1:9000/api"}
    )
    assert settings.api_base_url == "http://127.0.0.1:9000/api"


def test_timeout_must_be_finite_and_bounded() -> None:
    with pytest.raises(ConfigError, match="finite"):
        load_settings({"PATREON_ACCESS_TOKEN": "x", "PATREON_TIMEOUT_SECONDS": "nan"})
    with pytest.raises(ConfigError, match="at most"):
        load_settings({"PATREON_ACCESS_TOKEN": "x", "PATREON_TIMEOUT_SECONDS": "301"})


def test_oversized_token_and_cookie_files_are_rejected(tmp_path) -> None:
    token_file = tmp_path / "tokens.json"
    token_file.write_bytes(b"x" * 1_000_001)
    token_file.chmod(0o600)
    with pytest.raises(ConfigError, match="too large"):
        load_settings({"PATREON_TOKEN_FILE": str(token_file)})

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_bytes(b"x" * 65_537)
    cookie_file.chmod(0o600)
    with pytest.raises(ConfigError, match="too large"):
        load_settings({"PATREON_SESSION_COOKIE_FILE": str(cookie_file)})


def test_webhook_allowlist_is_parsed() -> None:
    settings = load_settings(
        {
            "PATREON_ACCESS_TOKEN": "x",
            "PATREON_WEBHOOK_ALLOWED_HOSTS": "hooks.example.com, api.example.com",
        }
    )
    assert settings.webhook_allowed_hosts == ("hooks.example.com", "api.example.com")


def test_env_access_token_ignores_stale_token_file_expiry(tmp_path) -> None:
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"access_token":"file-old","refresh_token":"file-refresh","expires_at":1}'
    )
    token_file.chmod(0o600)

    settings = load_settings(
        {
            "PATREON_TOKEN_FILE": str(token_file),
            "PATREON_ACCESS_TOKEN": "fresh-env",
        }
    )

    assert settings.access_token == "fresh-env"
    assert settings.expires_at is None


def test_env_cookie_skips_missing_cookie_file(tmp_path) -> None:
    settings = load_settings(
        {
            "PATREON_SESSION_COOKIE": "session_id=env",
            "PATREON_SESSION_COOKIE_FILE": str(tmp_path / "missing.txt"),
        }
    )

    assert settings.session_cookie == "session_id=env"


def test_credential_files_must_be_private_and_regular(tmp_path) -> None:
    token_file = tmp_path / "tokens.json"
    token_file.write_text('{"access_token":"x"}')
    token_file.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_settings({"PATREON_TOKEN_FILE": str(token_file)})

    token_file.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(token_file)
    with pytest.raises(ConfigError, match="must not be a symlink"):
        load_settings({"PATREON_TOKEN_FILE": str(link)})


def test_base_url_host_is_pinned_to_patreon_or_loopback() -> None:
    with pytest.raises(ConfigError, match="must point at www.patreon.com"):
        load_settings(
            {
                "PATREON_ACCESS_TOKEN": "x",
                "PATREON_WEB_BASE_URL": "https://attacker.example",
            }
        )


def test_settings_repr_redacts_credentials() -> None:
    settings = Settings(
        access_token="access-secret",
        refresh_token="refresh-secret",
        client_secret="client-secret",
        session_cookie="session=secret",
    )
    rendered = repr(settings)
    assert "access-secret" not in rendered
    assert "refresh-secret" not in rendered
    assert "client-secret" not in rendered
    assert "session=secret" not in rendered


def test_pii_opt_in_is_parsed() -> None:
    assert load_settings({"PATREON_ACCESS_TOKEN": "x"}).allow_pii is False
    assert load_settings({"PATREON_ACCESS_TOKEN": "x", "PATREON_ALLOW_PII": "1"}).allow_pii is True


def test_malformed_base_url_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="valid URL"):
        load_settings(
            {
                "PATREON_ACCESS_TOKEN": "x",
                "PATREON_WEB_BASE_URL": "https://[::1",
            }
        )
