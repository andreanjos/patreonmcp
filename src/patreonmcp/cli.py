"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from . import __version__
from .api import PatreonAPI
from .client import PatreonClient, PatreonError
from .config import ConfigError, Settings, load_settings
from .http_auth import BearerAuthMiddleware
from .post_cli import add_post_parser, run_post_command
from .server import create_server
from .web_api import PatreonWebAPI, PatreonWebClient

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_loopback_host(host: str) -> bool:
    # The MCP SDK auto-enables DNS-rebinding protection only for this exact set.
    # Do not broaden it to all of 127/8; the SDK would leave protection off.
    normalized = host.strip().lower().strip("[]")
    return normalized in LOOPBACK_HOSTS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patreonmcp",
        description="MCP server for the official Patreon API v2.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the MCP server.")
    serve.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    serve.add_argument("--port", type=int, default=8000, help="HTTP bind port.")
    serve.add_argument(
        "--allow-remote-http",
        action="store_true",
        help=(
            "Allow binding the HTTP transport beyond loopback. Requires "
            "PATREON_HTTP_BEARER_TOKEN unless --allow-unauthenticated-http is also set."
        ),
    )
    serve.add_argument(
        "--allow-unauthenticated-http",
        action="store_true",
        help=(
            "Permit remote HTTP without a bearer token. Only use behind an "
            "authenticated reverse proxy."
        ),
    )
    serve.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
        help="Log level written to stderr.",
    )

    add_post_parser(subparsers)
    subparsers.add_parser(
        "check",
        help="Verify OAuth credentials and/or the web post session.",
    )
    return parser


async def _check(settings: Settings) -> int:
    failed = False

    if settings.access_token or settings.can_refresh:
        try:
            async with PatreonClient(settings) as client:
                api = PatreonAPI(client)
                user = await api.get_identity(include_campaign=False)
                campaigns = await api.list_campaigns(include_tiers=False)
                name = user.full_name or user.first_name or user.vanity or user.id
                print(f"OAuth token authenticated as {name} (user {user.id}).")
                if not campaigns.campaigns:
                    print("No campaigns are visible to this token.")
                else:
                    print(f"Visible campaigns: {len(campaigns.campaigns)}")
                    for campaign in campaigns.campaigns:
                        label = campaign.name or campaign.creation_name or campaign.id
                        patrons = (
                            campaign.patron_count if campaign.patron_count is not None else "?"
                        )
                        print(f"  {campaign.id}  {label}  patrons={patrons}")
        except PatreonError as exc:
            print(f"patreonmcp: OAuth check failed: {exc}", file=sys.stderr)
            failed = True

    if settings.has_session:
        try:
            async with PatreonWebClient(settings) as web_client:
                web = PatreonWebAPI(web_client)
                campaign_id = await web.get_current_campaign_id()
                await web_client.check_csrf()
                print(
                    "Web post session authenticated for campaign "
                    f"{campaign_id}; CSRF ticket verified."
                )
        except PatreonError as exc:
            print(f"patreonmcp: web session check failed: {exc}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"

    if command == "serve" and args.command is None:
        # argparse does not apply subparser defaults when no subcommand is given.
        args.transport = "stdio"
        args.host = "127.0.0.1"
        args.port = 8000
        args.log_level = "WARNING"
        args.allow_remote_http = False
        args.allow_unauthenticated_http = False

    try:
        settings = load_settings().require_auth()
    except ConfigError as exc:
        print(f"patreonmcp: {exc}", file=sys.stderr)
        return 2

    if command == "check":
        try:
            return asyncio.run(_check(settings))
        except PatreonError as exc:
            print(f"patreonmcp: {exc}", file=sys.stderr)
            return 1

    if command == "post":
        try:
            return asyncio.run(run_post_command(settings, args))
        except PatreonError as exc:
            print(f"patreonmcp: {exc}", file=sys.stderr)
            return 1

    if args.transport == "streamable-http":
        if not 1 <= args.port <= 65535:
            print("patreonmcp: --port must be between 1 and 65535.", file=sys.stderr)
            return 2
        if not _is_loopback_host(args.host):
            if not args.allow_remote_http:
                print(
                    "patreonmcp: refusing to expose HTTP beyond loopback. Pass "
                    "--allow-remote-http and set PATREON_HTTP_BEARER_TOKEN.",
                    file=sys.stderr,
                )
                return 2
            if not settings.http_bearer_token and not args.allow_unauthenticated_http:
                print(
                    "patreonmcp: refusing remote HTTP without authentication. Set "
                    "PATREON_HTTP_BEARER_TOKEN or pass --allow-unauthenticated-http "
                    "behind an authenticated proxy.",
                    file=sys.stderr,
                )
                return 2
            if not settings.http_bearer_token:
                print(
                    "patreonmcp: warning: remote HTTP is unauthenticated; anyone who "
                    "can reach the port can call destructive tools.",
                    file=sys.stderr,
                )

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        mcp = create_server(settings)
    except ConfigError as exc:
        print(f"patreonmcp: {exc}", file=sys.stderr)
        return 2

    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        import uvicorn

        app = mcp.streamable_http_app(host=args.host)
        if settings.http_bearer_token:
            app = BearerAuthMiddleware(app, settings.http_bearer_token)
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
