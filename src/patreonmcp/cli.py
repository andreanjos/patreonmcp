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
from .server import create_server
from .web_api import PatreonWebAPI, PatreonWebClient


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
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
        help="Log level written to stderr.",
    )

    subparsers.add_parser(
        "check",
        help="Verify OAuth credentials and/or the web post session.",
    )
    return parser


async def _check(settings: Settings) -> int:
    if settings.access_token or settings.can_refresh:
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
                    patrons = campaign.patron_count if campaign.patron_count is not None else "?"
                    print(f"  {campaign.id}  {label}  patrons={patrons}")

    if settings.has_session:
        async with PatreonWebClient(settings) as web_client:
            web = PatreonWebAPI(web_client)
            campaign_id = await web.get_current_campaign_id()
            print(f"Web post session authenticated for campaign {campaign_id}.")
    return 0


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
        mcp.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
