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


def test_remote_http_requires_a_token_or_explicit_unauthenticated_flag(monkeypatch, capsys) -> None:
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
            "--allow-remote-http",
        ]
    )

    assert code == 2
    assert called == []
    assert "without authentication" in capsys.readouterr().err


def test_post_commands_are_registered() -> None:
    from patreonmcp.cli import _parser

    args = _parser().parse_args(["post", "create", "Title", "--content", "Body"])
    assert args.command == "post"
    assert args.post_command == "create"
    assert args.title == "Title"
    assert args.content == "Body"


async def test_post_delete_requires_yes() -> None:
    from patreonmcp.cli import _parser
    from patreonmcp.config import Settings
    from patreonmcp.post_cli import run_post_command

    args = _parser().parse_args(["post", "delete", "p1"])
    code = await run_post_command(Settings(access_token="x"), args)
    assert code == 2
