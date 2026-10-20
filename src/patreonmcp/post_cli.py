"""CLI commands for inspecting and managing posts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .api import PatreonAPI
from .client import PatreonClient, PatreonError
from .config import Settings
from .models import Post
from .web_api import (
    MAX_CONTENT_CHARS,
    PatreonWebAPI,
    PatreonWebClient,
)


def add_post_parser(subparsers: Any) -> None:
    post = subparsers.add_parser("post", help="List, create, edit, publish, or delete posts.")
    post_sub = post.add_subparsers(dest="post_command", required=True)

    list_p = post_sub.add_parser("list", help="List posts or drafts.")
    list_p.add_argument("--drafts", action="store_true", help="List unpublished drafts.")
    list_p.add_argument("--campaign-id", default=None)
    list_p.add_argument("--limit", type=int, default=20, help="Results per page.")
    list_p.add_argument("--cursor", default=None)
    list_p.add_argument("--include-content", action="store_true")
    list_p.add_argument("--json", dest="as_json", action="store_true")

    get_p = post_sub.add_parser("get", help="Show one post.")
    get_p.add_argument("post_id")
    get_p.add_argument("--managed", action="store_true", help="Use the web API (drafts).")
    get_p.add_argument("--content", action="store_true", help="Print post content.")
    get_p.add_argument("--json", dest="as_json", action="store_true")

    create_p = post_sub.add_parser("create", help="Create a draft or published post.")
    create_p.add_argument("title")
    _content_args(create_p, required=False)
    _post_fields(create_p)
    create_p.add_argument("--publish", action="store_true")
    create_p.add_argument("--json", dest="as_json", action="store_true")

    update_p = post_sub.add_parser("update", help="Edit an existing post.")
    update_p.add_argument("post_id")
    update_p.add_argument("--title")
    _content_args(update_p, required=False)
    _post_fields(update_p)
    update_p.add_argument("--clear-tags", action="store_true")
    publish_group = update_p.add_mutually_exclusive_group()
    publish_group.add_argument("--publish", dest="publish", action="store_true", default=None)
    publish_group.add_argument("--no-publish", dest="publish", action="store_false")
    update_p.add_argument("--json", dest="as_json", action="store_true")

    publish_p = post_sub.add_parser("publish", help="Publish a draft now.")
    publish_p.add_argument("post_id")
    publish_p.add_argument("--json", dest="as_json", action="store_true")

    delete_p = post_sub.add_parser("delete", help="Delete a post permanently.")
    delete_p.add_argument("post_id")
    delete_p.add_argument("--yes", action="store_true", help="Confirm permanent deletion.")
    delete_p.add_argument("--json", dest="as_json", action="store_true")


def _content_args(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--content", default=None)
    group.add_argument(
        "--content-file",
        default=None,
        metavar="PATH",
        help="Read content from a file, or '-' for stdin.",
    )


def _post_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--visibility", choices=("public", "paid", "tiers"), default=None)
    parser.add_argument("--tier-id", action="append", default=None)
    parser.add_argument("--tag", action="append", default=None)
    parser.add_argument(
        "--schedule",
        dest="scheduled_for",
        default=None,
        help="ISO 8601 publish time; implies publish.",
    )
    parser.add_argument("--teaser", dest="teaser_text", default=None)
    parser.add_argument("--allow-html", action="store_true")
    parser.add_argument("--campaign-id", default=None)


def _content_from_args(args: argparse.Namespace) -> str | None:
    if args.content_file is None:
        return args.content
    if args.content_file == "-":
        content = sys.stdin.read(MAX_CONTENT_CHARS + 1)
    else:
        path = Path(args.content_file).expanduser()
        if path.stat().st_size > MAX_CONTENT_CHARS:
            raise PatreonError(f"content file must be at most {MAX_CONTENT_CHARS} bytes.")
        content = path.read_text(encoding="utf-8")
    if len(content) > MAX_CONTENT_CHARS:
        raise PatreonError(f"content must be at most {MAX_CONTENT_CHARS} characters.")
    return content


def _print_post(post: Post, as_json: bool, *, show_content: bool = False) -> None:
    if as_json:
        print(json.dumps(post.model_dump(mode="json"), indent=2))
        return
    status = post.published_at or post.scheduled_for or ("draft" if post.is_draft else "")
    print(f"{post.id}\t{post.title or '(untitled)'}\t{post.post_type or ''}\t{status}")
    if post.url or post.patreon_url:
        print(post.url or post.patreon_url)
    if show_content and post.content:
        print()
        print(post.content)


def _print_posts(page: Any, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "posts": [post.model_dump(mode="json") for post in page.posts],
                    "next_cursor": page.next_cursor,
                    "total": page.total,
                },
                indent=2,
            )
        )
        return
    if not page.posts:
        print("No posts found.")
        return
    for post in page.posts:
        _print_post(post, False)
    if page.next_cursor:
        print(f"\nnext_cursor: {page.next_cursor}")


async def run_post_command(settings: Settings, args: argparse.Namespace) -> int:
    command = args.post_command

    if command == "list":
        if args.drafts:
            async with PatreonWebClient(settings) as client:
                page = await PatreonWebAPI(client).list_drafts(
                    args.campaign_id,
                    cursor=args.cursor,
                    page_size=args.limit,
                    include_content=args.include_content,
                )
        else:
            async with PatreonClient(settings) as client:
                page = await PatreonAPI(client).list_posts(
                    args.campaign_id,
                    cursor=args.cursor,
                    page_size=args.limit,
                    include_content=args.include_content,
                )
        _print_posts(page, args.as_json)
        return 0

    if command == "get":
        if args.managed:
            async with PatreonWebClient(settings) as client:
                post = await PatreonWebAPI(client).get_post(args.post_id)
        else:
            async with PatreonClient(settings) as client:
                post = await PatreonAPI(client).get_post(args.post_id)
        _print_post(post, args.as_json, show_content=args.content)
        return 0

    if command == "create":
        content = _content_from_args(args)
        publish = args.publish or bool(args.scheduled_for)
        async with PatreonWebClient(settings) as client:
            post = await PatreonWebAPI(client).create_post(
                title=args.title,
                content=content,
                teaser_text=args.teaser_text,
                visibility=args.visibility or "paid",
                tier_ids=args.tier_id,
                publish=publish,
                scheduled_for=args.scheduled_for,
                tags=args.tag,
                campaign_id=args.campaign_id,
                allow_html=args.allow_html,
            )
        _print_post(post, args.as_json, show_content=True)
        return 0

    if command == "update":
        content = _content_from_args(args)
        publish = args.publish
        if args.scheduled_for and publish is None:
            publish = True
        async with PatreonWebClient(settings) as client:
            if args.clear_tags and args.tag:
                raise PatreonError("Use --tag or --clear-tags, not both.")
            post = await PatreonWebAPI(client).update_post(
                args.post_id,
                title=args.title,
                content=content,
                teaser_text=args.teaser_text,
                visibility=args.visibility,
                tier_ids=args.tier_id,
                publish=publish,
                scheduled_for=args.scheduled_for,
                tags=[] if args.clear_tags else args.tag,
                campaign_id=args.campaign_id,
                allow_html=args.allow_html,
            )
        _print_post(post, args.as_json, show_content=True)
        return 0

    if command == "publish":
        async with PatreonWebClient(settings) as client:
            post = await PatreonWebAPI(client).update_post(args.post_id, publish=True)
        _print_post(post, args.as_json, show_content=False)
        return 0

    if command == "delete":
        if not args.yes:
            print("patreonmcp: refusing deletion without --yes.", file=sys.stderr)
            return 2
        async with PatreonWebClient(settings) as client:
            result = await PatreonWebAPI(client).delete_post(args.post_id)
        if args.as_json:
            print(json.dumps(result.model_dump(), indent=2))
        else:
            print(f"Deleted post {result.id}.")
        return 0

    raise PatreonError(f"Unknown post command: {command}")
