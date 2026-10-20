"""MCP server construction and tool definitions."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal, TypeVar

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .api import PatreonAPI
from .client import PatreonClient, PatreonError
from .config import Settings
from .models import (
    Campaign,
    CampaignList,
    DeleteResult,
    Live,
    Member,
    MemberPage,
    Post,
    PostPage,
    User,
    Webhook,
    WebhookList,
)
from .web_api import PatreonWebAPI, PatreonWebClient

T = TypeVar("T")


@dataclass
class AppContext:
    api: PatreonAPI
    web: PatreonWebAPI | None


READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
CREATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
UPDATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
DELETE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

INSTRUCTIONS = """\
Patreon creator data via the official Patreon API v2.

Start with get_identity or list_campaigns. Tools that need a campaign accept an
optional campaign_id; when it is omitted and exactly one campaign is visible,
that campaign is used. If several are visible, pass campaign_id explicitly.

Members and posts are cursor-paginated: pass next_cursor back as cursor. Member
email, creator notes, and shipping addresses are opt-in and require matching
OAuth scopes.

The official API is read-only for campaigns, tiers, members, and posts.
Webhooks and early-access Lives are writable through the official API.

When PATREON_SESSION_COOKIE or PATREON_SESSION_COOKIE_FILE is configured, the
create_post, update_post, publish_post, delete_post, list_drafts, and
get_managed_post tools use Patreon's own browser API. These are undocumented
endpoints, can change without notice, and support text posts only.
"""


def _context(ctx: Context) -> AppContext:
    app = ctx.request_context.lifespan_context
    if not isinstance(app, AppContext):
        raise ToolError("Patreon API context is unavailable.")
    return app


def _api(ctx: Context) -> PatreonAPI:
    return _context(ctx).api


def _web(ctx: Context) -> PatreonWebAPI:
    web = _context(ctx).web
    if web is None:
        raise ToolError(
            "Post write tools require a Patreon web session. Set "
            "PATREON_SESSION_COOKIE or PATREON_SESSION_COOKIE_FILE."
        )
    return web


async def _guard(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except PatreonError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def create_server(
    settings: Settings,
    *,
    api: PatreonAPI | None = None,
    web_api: PatreonWebAPI | None = None,
) -> MCPServer:
    """Build the MCP server. Tests may inject preconfigured API objects."""

    settings.require_auth()

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        if api is not None or web_api is not None:
            yield AppContext(
                api=api or PatreonAPI(PatreonClient(settings)),
                web=web_api,
            )
            return

        async with PatreonClient(settings) as client:
            if not settings.has_session:
                yield AppContext(api=PatreonAPI(client), web=None)
                return
            async with PatreonWebClient(settings) as web_client:
                yield AppContext(
                    api=PatreonAPI(client),
                    web=PatreonWebAPI(web_client),
                )

    mcp = MCPServer(
        "patreonmcp",
        title="Patreon MCP",
        description="Patreon creator data, webhooks, and Lives via the official API v2.",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)

    @mcp.tool(annotations=READ_ONLY)
    async def get_identity(
        ctx: Context,
        include_campaign: Annotated[
            bool, Field(description="Include the creator's campaign in the result.")
        ] = True,
        include_email: Annotated[
            bool,
            Field(description=("Request the user's email. Requires the identity[email] scope.")),
        ] = False,
    ) -> User:
        """Get the authenticated Patreon user (the creator behind the token)."""

        return await _guard(
            _api(ctx).get_identity(include_campaign=include_campaign, include_email=include_email)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_campaigns(
        ctx: Context,
        include_tiers: Annotated[
            bool, Field(description="Include membership tiers for each campaign.")
        ] = True,
        include_creator: Annotated[
            bool, Field(description="Include the campaign creator profile.")
        ] = False,
    ) -> CampaignList:
        """List campaigns owned by the authenticated creator."""

        return await _guard(
            _api(ctx).list_campaigns(include_tiers=include_tiers, include_creator=include_creator)
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_campaign(
        ctx: Context,
        campaign_id: Annotated[
            str | None,
            Field(
                description=("Campaign ID. Omit only when the token can see exactly one campaign.")
            ),
        ] = None,
        include_tiers: Annotated[bool, Field(description="Include membership tiers.")] = True,
        include_creator: Annotated[bool, Field(description="Include the creator profile.")] = True,
    ) -> Campaign:
        """Get one campaign with its tiers and optionally its creator."""

        return await _guard(
            _api(ctx).get_campaign(
                campaign_id,
                include_tiers=include_tiers,
                include_creator=include_creator,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_members(
        ctx: Context,
        campaign_id: Annotated[
            str | None,
            Field(
                description=("Campaign ID. Omit only when the token can see exactly one campaign.")
            ),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="next_cursor from a previous list_members call."),
        ] = None,
        page_size: Annotated[
            int, Field(ge=1, le=1000, description="Results per page (1-1000).")
        ] = 20,
        include_tiers: Annotated[
            bool, Field(description="Include the tiers each member is entitled to.")
        ] = True,
        include_user: Annotated[
            bool, Field(description="Include the patron's public user profile.")
        ] = False,
        include_email: Annotated[
            bool,
            Field(
                description=(
                    "Include patron email addresses. Requires the "
                    "campaigns.members[email] scope; patrons may hide it."
                )
            ),
        ] = False,
        include_note: Annotated[
            bool, Field(description="Include the creator's private note about each patron.")
        ] = False,
        include_address: Annotated[
            bool,
            Field(
                description=(
                    "Include shipping addresses. Requires the "
                    "campaigns.members.address scope; handle as sensitive data."
                )
            ),
        ] = False,
    ) -> MemberPage:
        """List patrons for a campaign with cursor pagination."""

        return await _guard(
            _api(ctx).list_members(
                campaign_id,
                cursor=cursor,
                page_size=page_size,
                include_tiers=include_tiers,
                include_user=include_user,
                include_email=include_email,
                include_note=include_note,
                include_address=include_address,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_member(
        ctx: Context,
        member_id: Annotated[str, Field(description="Member UUID, not the user ID.")],
        include_tiers: Annotated[
            bool, Field(description="Include the tiers the member is entitled to.")
        ] = True,
        include_user: Annotated[
            bool, Field(description="Include the patron's public user profile.")
        ] = False,
        include_email: Annotated[
            bool,
            Field(
                description=(
                    "Include the member's email. Requires the campaigns.members[email] scope."
                )
            ),
        ] = False,
        include_note: Annotated[
            bool, Field(description="Include the creator's private note about the patron.")
        ] = False,
        include_address: Annotated[
            bool,
            Field(
                description=(
                    "Include the member's shipping address. Requires the "
                    "campaigns.members.address scope."
                )
            ),
        ] = False,
    ) -> Member:
        """Get one patron by member UUID."""

        return await _guard(
            _api(ctx).get_member(
                member_id,
                include_tiers=include_tiers,
                include_user=include_user,
                include_email=include_email,
                include_note=include_note,
                include_address=include_address,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def list_posts(
        ctx: Context,
        campaign_id: Annotated[
            str | None,
            Field(
                description=("Campaign ID. Omit only when the token can see exactly one campaign.")
            ),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="next_cursor from a previous list_posts call."),
        ] = None,
        page_size: Annotated[
            int, Field(ge=1, le=1000, description="Results per page (1-1000).")
        ] = 20,
        include_content: Annotated[
            bool,
            Field(
                description=(
                    "Include full post HTML. Off by default because post bodies can be large."
                )
            ),
        ] = False,
    ) -> PostPage:
        """List posts for a campaign with cursor pagination."""

        return await _guard(
            _api(ctx).list_posts(
                campaign_id,
                cursor=cursor,
                page_size=page_size,
                include_content=include_content,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_post(
        ctx: Context,
        post_id: Annotated[str, Field(description="Patreon post ID.")],
    ) -> Post:
        """Get one post, including its full HTML content."""

        return await _guard(_api(ctx).get_post(post_id))

    @mcp.tool(annotations=CREATE)
    async def create_post(
        ctx: Context,
        title: Annotated[str, Field(description="Post title.")],
        content: Annotated[
            str | None,
            Field(description=("Post body as HTML or plain text. The web editor stores HTML.")),
        ] = None,
        visibility: Annotated[
            Literal["public", "paid", "tiers"],
            Field(
                description=(
                    "public = everyone; paid = all paying patrons; tiers = only "
                    "the tier_ids listed."
                )
            ),
        ] = "paid",
        tier_ids: Annotated[
            list[str] | None,
            Field(
                description=("Tier IDs allowed to view the post. Required when visibility='tiers'.")
            ),
        ] = None,
        publish: Annotated[
            bool,
            Field(
                description=(
                    "false creates a draft; true publishes immediately or schedules "
                    "when scheduled_for is set."
                )
            ),
        ] = False,
        scheduled_for: Annotated[
            str | None,
            Field(
                description=(
                    "ISO 8601 publish time with timezone, for example "
                    "2026-10-20T18:00:00Z. Requires publish=true."
                )
            ),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(description="Optional user-defined post tags."),
        ] = None,
        teaser_text: Annotated[
            str | None,
            Field(description="Optional plain-text teaser shown before the paywall."),
        ] = None,
        campaign_id: Annotated[
            str | None,
            Field(
                description=(
                    "Campaign ID, used only to resolve tier_ids. Omit to use the "
                    "web session's current campaign."
                )
            ),
        ] = None,
    ) -> Post:
        """Create a text post through Patreon's web editor API.

        Requires PATREON_SESSION_COOKIE or PATREON_SESSION_COOKIE_FILE. This uses
        Patreon's undocumented browser API, not the official OAuth API, and can
        break when Patreon changes its web app. Only text posts are supported.
        """

        return await _guard(
            _web(ctx).create_post(
                title=title,
                content=content,
                teaser_text=teaser_text,
                visibility=visibility,
                tier_ids=tier_ids,
                publish=publish,
                scheduled_for=scheduled_for,
                tags=tags,
                campaign_id=campaign_id,
            )
        )

    @mcp.tool(annotations=UPDATE)
    async def update_post(
        ctx: Context,
        post_id: Annotated[str, Field(description="Patreon post ID.")],
        title: Annotated[str | None, Field(description="New title.")] = None,
        content: Annotated[str | None, Field(description="New body as HTML or plain text.")] = None,
        visibility: Annotated[
            Literal["public", "paid", "tiers"] | None,
            Field(description="New audience setting."),
        ] = None,
        tier_ids: Annotated[
            list[str] | None,
            Field(description="Tier IDs allowed to view the post."),
        ] = None,
        publish: Annotated[
            bool | None,
            Field(
                description=(
                    "true publishes immediately or schedules with scheduled_for; "
                    "false saves without publishing."
                )
            ),
        ] = None,
        scheduled_for: Annotated[
            str | None,
            Field(description="ISO 8601 publish time with timezone."),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(description="Replacement user-defined tag list."),
        ] = None,
        teaser_text: Annotated[str | None, Field(description="New plain-text teaser.")] = None,
        campaign_id: Annotated[
            str | None,
            Field(description="Campaign ID used to resolve tier_ids."),
        ] = None,
    ) -> Post:
        """Update or publish an existing post through Patreon's web editor API.

        Requires a web session. Pass publish=true to publish now, or publish=true
        plus scheduled_for to schedule. Omitting publish saves without changing
        publication state.
        """

        return await _guard(
            _web(ctx).update_post(
                post_id,
                title=title,
                content=content,
                teaser_text=teaser_text,
                visibility=visibility,
                tier_ids=tier_ids,
                publish=publish,
                scheduled_for=scheduled_for,
                tags=tags,
                campaign_id=campaign_id,
            )
        )

    @mcp.tool(annotations=UPDATE)
    async def publish_post(
        ctx: Context,
        post_id: Annotated[str, Field(description="Patreon post ID.")],
    ) -> Post:
        """Publish an existing draft through Patreon's web editor API."""

        return await _guard(_web(ctx).update_post(post_id, publish=True))

    @mcp.tool(annotations=DELETE)
    async def delete_post(
        ctx: Context,
        post_id: Annotated[str, Field(description="Patreon post ID.")],
    ) -> DeleteResult:
        """Delete a post through Patreon's web editor API.

        This is destructive and cannot be undone.
        """

        return await _guard(_web(ctx).delete_post(post_id))

    @mcp.tool(annotations=READ_ONLY)
    async def get_managed_post(
        ctx: Context,
        post_id: Annotated[str, Field(description="Patreon post ID.")],
    ) -> Post:
        """Get a post, draft, or scheduled post through the web editor API."""

        return await _guard(_web(ctx).get_post(post_id))

    @mcp.tool(annotations=READ_ONLY)
    async def list_drafts(
        ctx: Context,
        campaign_id: Annotated[
            str | None,
            Field(description="Campaign ID; omit to use the web session's campaign."),
        ] = None,
        cursor: Annotated[
            str | None,
            Field(description="next_cursor from a previous list_drafts call."),
        ] = None,
        page_size: Annotated[
            int, Field(ge=1, le=1000, description="Results per page (1-1000).")
        ] = 20,
    ) -> PostPage:
        """List unpublished drafts for a campaign through the web editor API."""

        return await _guard(_web(ctx).list_drafts(campaign_id, cursor=cursor, page_size=page_size))

    @mcp.tool(annotations=READ_ONLY)
    async def list_webhooks(ctx: Context) -> WebhookList:
        """List this API client's webhooks, including their HMAC signing secrets."""

        return await _guard(_api(ctx).list_webhooks())

    @mcp.tool(annotations=CREATE)
    async def create_webhook(
        ctx: Context,
        uri: Annotated[
            str, Field(description="Public HTTPS endpoint that receives Patreon events.")
        ],
        triggers: Annotated[
            list[str],
            Field(
                description=(
                    "Events to subscribe to: members:create, members:update, "
                    "members:delete, members:pledge:create, members:pledge:update, "
                    "members:pledge:delete, posts:publish, posts:update, posts:delete."
                )
            ),
        ],
        campaign_id: Annotated[
            str | None,
            Field(
                description=("Campaign ID. Omit only when the token can see exactly one campaign.")
            ),
        ] = None,
    ) -> Webhook:
        """Create a webhook. The response includes the HMAC signing secret."""

        return await _guard(_api(ctx).create_webhook(uri, triggers, campaign_id=campaign_id))

    @mcp.tool(annotations=UPDATE)
    async def update_webhook(
        ctx: Context,
        webhook_id: Annotated[str, Field(description="Webhook ID.")],
        uri: Annotated[str | None, Field(description="New HTTPS endpoint.")] = None,
        triggers: Annotated[
            list[str] | None, Field(description="Replacement trigger list.")
        ] = None,
        paused: Annotated[
            bool | None,
            Field(
                description=(
                    "Set false to resume and redeliver queued events after failures; true to pause."
                )
            ),
        ] = None,
    ) -> Webhook:
        """Update a webhook's URI, triggers, or paused state."""

        return await _guard(
            _api(ctx).update_webhook(webhook_id, uri=uri, triggers=triggers, paused=paused)
        )

    @mcp.tool(annotations=DELETE)
    async def delete_webhook(
        ctx: Context,
        webhook_id: Annotated[str, Field(description="Webhook ID.")],
    ) -> DeleteResult:
        """Delete a webhook created by this API client."""

        return await _guard(_api(ctx).delete_webhook(webhook_id))

    @mcp.tool(annotations=CREATE)
    async def create_live(
        ctx: Context,
        title: Annotated[str, Field(description="Livestream title.")],
        state: Annotated[
            Literal["pre_live", "live"],
            Field(
                description=(
                    "pre_live schedules the stream (scheduled_for required); live starts now."
                )
            ),
        ],
        description: Annotated[str | None, Field(description="Livestream description.")] = None,
        scheduled_for: Annotated[
            str | None,
            Field(
                description=(
                    "ISO 8601 start time with timezone, required for pre_live. "
                    "Example: 2026-10-20T18:00:00Z."
                )
            ),
        ] = None,
        live_access_rule_ids: Annotated[
            list[int] | None,
            Field(
                description=(
                    "Access rule IDs from the campaign's live_access_rules relationship. "
                    "Controls which tiers can view the stream."
                )
            ),
        ] = None,
    ) -> Live:
        """Create an early-access Patreon Live livestream."""

        return await _guard(
            _api(ctx).create_live(
                title=title,
                state=state,
                description=description,
                scheduled_for=scheduled_for,
                live_access_rule_ids=live_access_rule_ids,
            )
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_live(
        ctx: Context,
        live_id: Annotated[str, Field(description="Live ID.")],
    ) -> Live:
        """Get a livestream's state and RTMP connection details, including its stream key."""

        return await _guard(_api(ctx).get_live(live_id))

    @mcp.tool(annotations=UPDATE)
    async def update_live(
        ctx: Context,
        live_id: Annotated[str, Field(description="Live ID.")],
        state: Annotated[
            Literal["live", "live_ended"],
            Field(
                description=(
                    "pre_live -> live -> live_ended. No transitions are allowed after live_ended."
                )
            ),
        ],
    ) -> Live:
        """Start or end a livestream. Patreon enforces valid state transitions."""

        return await _guard(_api(ctx).update_live(live_id, state))

    return mcp
