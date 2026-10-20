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


def test_init_generates_stdio_and_http_configs(capsys) -> None:
    import json

    from patreonmcp.cli import main

    assert main(["init"]) == 0
    stdio = json.loads(capsys.readouterr().out)
    entry = stdio["mcpServers"]["patreon"]
    assert entry["command"] == "uv"
    assert entry["args"][-2:] == ["patreonmcp", "serve"]
    assert entry["env"]["PATREON_ACCESS_TOKEN"].startswith("REPLACE_")

    assert main(["init", "--http", "--url", "http://127.0.0.1:9000/mcp"]) == 0
    http = json.loads(capsys.readouterr().out)
    entry = http["mcpServers"]["patreon"]
    assert entry["url"] == "http://127.0.0.1:9000/mcp"
    assert "Authorization" in entry["headers"]


def test_init_writes_private_file(tmp_path) -> None:
    import json
    import stat

    from patreonmcp.cli import main

    path = tmp_path / "mcp.json"
    assert main(["init", "--output", str(path)]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["mcpServers"]["patreon"]["command"] == "uv"


def test_config_summary_redacts_secrets() -> None:
    import json

    from patreonmcp.cli import _config_summary
    from patreonmcp.config import Settings

    data = _config_summary(
        Settings(
            access_token="access-secret",
            refresh_token="refresh-secret",
            client_secret="client-secret",
            session_cookie="session-secret",
        )
    )
    rendered = json.dumps(data)
    assert "access-secret" not in rendered
    assert "refresh-secret" not in rendered
    assert "client-secret" not in rendered
    assert "session-secret" not in rendered
    assert data["oauth"]["access_token_configured"] is True
    assert data["web_session"]["cookie_configured"] is True


def test_init_refuses_to_overwrite_without_force(tmp_path) -> None:
    from patreonmcp.cli import main

    path = tmp_path / "mcp.json"
    path.write_text("existing")
    assert main(["init", "--output", str(path)]) == 2
    assert path.read_text() == "existing"
    assert main(["init", "--output", str(path), "--force"]) == 0
