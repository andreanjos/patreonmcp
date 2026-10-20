from __future__ import annotations

import pytest

from patreonmcp.cli import _is_loopback_host


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_hosts(host: str) -> None:
    assert _is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "127.0.0.2", "127.1", "192.168.1.5", "example.com", "::"]
)
def test_non_loopback_hosts(host: str) -> None:
    assert not _is_loopback_host(host)


def test_remote_http_requires_explicit_opt_in(monkeypatch, capsys) -> None:
    import patreonmcp.cli as cli
    from patreonmcp.config import Settings

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(access_token="x"))
    called: list[object] = []
    monkeypatch.setattr(cli, "create_server", lambda settings: called.append(settings))

    code = cli.main(
        [
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
        ]
    )

    assert code == 2
    assert called == []
    assert "refusing to expose" in capsys.readouterr().err
