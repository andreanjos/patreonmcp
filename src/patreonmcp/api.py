"""Patreon API v2 endpoint wrappers and JSON:API normalization."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .client import PatreonClient, PatreonError
from .models import (
    Address,
    Campaign,
    CampaignList,
    DeleteResult,
    Live,
    Member,
    MemberPage,
    Post,
    PostPage,
    Tier,
    User,
    UserSummary,
    Webhook,
    WebhookList,
)

USER_FIELDS = (
    "about",
    "created",
    "full_name",
    "first_name",
    "last_name",
    "image_url",
    "thumb_url",
    "url",
    "is_creator",
    "vanity",
)
CAMPAIGN_FIELDS = (
    "created_at",
    "creation_name",
    "currency",
    "image_small_url",
    "image_url",
    "is_charged_immediately",
    "is_eligible_for_live",
    "is_monthly",
    "is_nsfw",
    "name",
    "one_liner",
    "patron_count",
    "pay_per_name",
    "pledge_url",
    "published_at",
    "show_earnings",
    "summary",
    "url",
    "vanity",
)
TIER_FIELDS = (
    "amount_cents",
    "created_at",
    "description",
    "edited_at",
    "image_url",
    "patron_count",
    "post_count",
    "published",
    "published_at",
    "remaining",
    "requires_shipping",
    "title",
    "unpublished_at",
    "url",
    "user_limit",
)
MEMBER_FIELDS = (
    "campaign_lifetime_support_cents",
    "currently_entitled_amount_cents",
    "full_name",
    "is_free_trial",
    "is_gifted",
    "last_charge_date",
    "last_charge_status",
    "next_charge_date",
    "patron_status",
    "pledge_cadence",
    "pledge_relationship_start",
    "will_pay_amount_cents",
)
POST_FIELDS = (
    "app_status",
    "embed_data",
    "embed_url",
    "is_paid",
    "is_public",
    "published_at",
    "tiers",
    "title",
    "url",
)
WEBHOOK_FIELDS = (
    "last_attempted_at",
    "num_consecutive_times_failed",
    "paused",
    "secret",
    "triggers",
    "uri",
)
LIVE_FIELDS = (
    "description",
    "rtmp_url",
    "scheduled_for",
    "state",
    "stream_key",
    "title",
)
ADDRESS_FIELDS = (
    "addressee",
    "city",
    "country",
    "line_1",
    "line_2",
    "phone_number",
    "postal_code",
    "state",
)

WEBHOOK_TRIGGERS = frozenset(
    {
        "members:create",
        "members:update",
        "members:delete",
        "members:pledge:create",
        "members:pledge:update",
        "members:pledge:delete",
        "posts:publish",
        "posts:update",
        "posts:delete",
    }
)
LIVE_CREATE_STATES = frozenset({"pre_live", "live"})
LIVE_UPDATE_STATES = frozenset({"live", "live_ended"})

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_POST_CONTENT_CHARS = 100_000
MAX_WEBHOOK_URI_CHARS = 2048
MAX_WEBHOOK_TRIGGERS = 50
MAX_LIVE_TITLE_CHARS = 500
MAX_LIVE_DESCRIPTION_CHARS = 5_000
MAX_LIVE_ACCESS_RULES = 100


def _csv(values: Sequence[str]) -> str:
    return ",".join(values)


def _validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise PatreonError(
            f"Invalid {label}. Expected 1-128 letters, digits, underscores, or hyphens."
        )
    return value


def _validate_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or len(cursor) > 4096:
        raise PatreonError("cursor must be a string of at most 4096 characters.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in cursor):
        raise PatreonError("cursor must not contain control characters.")
    return cursor


def _validate_page_size(page_size: int) -> int:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
        raise PatreonError("page_size must be an integer between 1 and 1000.")
    return page_size


def _validate_webhook_uri(uri: str, allowed_hosts: Sequence[str] = ()) -> str:
    if not isinstance(uri, str):
        raise PatreonError("Webhook uri must be a string.")
    if len(uri) > MAX_WEBHOOK_URI_CHARS:
        raise PatreonError(f"Webhook uri must be at most {MAX_WEBHOOK_URI_CHARS} characters.")
    try:
        parsed = urlparse(uri)
        scheme = parsed.scheme
        netloc = parsed.netloc
        username = parsed.username
        password = parsed.password
        host = parsed.hostname or ""
    except ValueError as exc:
        raise PatreonError("Webhook uri is not a valid URL.") from exc
    if scheme != "https" or not netloc or not host:
        raise PatreonError("Webhook uri must be an absolute https:// URL with a host.")
    if username or password:
        raise PatreonError("Webhook uri must not contain embedded credentials.")
    if not allowed_hosts:
        raise PatreonError(
            "Webhook writes are disabled. Set PATREON_WEBHOOK_ALLOWED_HOSTS to "
            "the hostnames that may receive Patreon events."
        )
    if "*" not in allowed_hosts and host not in allowed_hosts:
        raise PatreonError("Webhook uri host is not in PATREON_WEBHOOK_ALLOWED_HOSTS.")
    if host.lower() in {"localhost", "localhost."} or host.lower().endswith(".localhost"):
        raise PatreonError("Webhook uri must not target a local hostname.")
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))
        except (OSError, ValueError, OverflowError):
            if re.fullmatch(r"[0-9.]+", host):
                raise PatreonError(
                    "Webhook uri must use a canonical hostname, not a numeric IP form."
                ) from None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or getattr(address, "ipv4_mapped", None) is not None
    ):
        raise PatreonError(
            "Webhook uri must not target a private, loopback, link-local, "
            "reserved, multicast, or unspecified address."
        )
    return uri


def _validate_triggers(triggers: Sequence[str]) -> list[str]:
    if isinstance(triggers, str):
        raise PatreonError("triggers must be a list of strings.")
    if not triggers:
        raise PatreonError("At least one webhook trigger is required.")
    if len(triggers) > MAX_WEBHOOK_TRIGGERS:
        raise PatreonError(f"At most {MAX_WEBHOOK_TRIGGERS} webhook triggers are allowed.")
    if any(not isinstance(item, str) for item in triggers):
        raise PatreonError("Each webhook trigger must be a string.")
    unknown = sorted(set(triggers) - WEBHOOK_TRIGGERS)
    if unknown:
        allowed = ", ".join(sorted(WEBHOOK_TRIGGERS))
        raise PatreonError(f"Unknown webhook trigger(s): {', '.join(unknown)}. Allowed: {allowed}.")
    return list(dict.fromkeys(triggers))


def _normalize_scheduled_for(value: str) -> str:
    if not isinstance(value, str):
        raise PatreonError("scheduled_for must be a string.")
    raw = value.strip()
    if not raw:
        raise PatreonError("scheduled_for must not be empty.")
    if len(raw) > 64:
        raise PatreonError("scheduled_for is too long.")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PatreonError(
            "scheduled_for must be an ISO 8601 datetime, for example 2026-10-20T18:00:00Z."
        ) from exc
    if parsed.tzinfo is None:
        raise PatreonError("scheduled_for must include a timezone offset or Z.")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _attrs(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    attributes = resource.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _relationships(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    relationships = resource.get("relationships")
    return relationships if isinstance(relationships, dict) else {}


def _relationship_ids(resource: Mapping[str, Any], name: str) -> list[str]:
    relationship = _relationships(resource).get(name)
    if not isinstance(relationship, dict):
        return []
    data = relationship.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [
        str(item["id"]) for item in data if isinstance(item, dict) and item.get("id") is not None
    ]


def _resource_list(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _included(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    included = payload.get("included")
    if not isinstance(included, list):
        return []
    return [item for item in included if isinstance(item, dict)]


def _included_index(payload: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    for resource in _included(payload):
        resource_type = resource.get("type")
        resource_id = resource.get("id")
        if isinstance(resource_type, str) and resource_id is not None:
            index[(resource_type, str(resource_id))] = resource
    return index


def _included_by_type(payload: Mapping[str, Any], resource_type: str) -> list[Mapping[str, Any]]:
    return [item for item in _included(payload) if item.get("type") == resource_type]


def _next_cursor(payload: Mapping[str, Any]) -> str | None:
    meta = payload.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    cursors = pagination.get("cursors") if isinstance(pagination, dict) else None
    if isinstance(cursors, dict) and cursors.get("next"):
        return str(cursors["next"])

    links = payload.get("links")
    next_link = links.get("next") if isinstance(links, dict) else None
    if isinstance(next_link, str):
        query = parse_qs(urlparse(next_link).query)
        cursor = query.get("page[cursor]")
        if cursor:
            return cursor[-1]
    return None


def _total(payload: Mapping[str, Any]) -> int | None:
    meta = payload.get("meta")
    pagination = meta.get("pagination") if isinstance(meta, dict) else None
    total = pagination.get("total") if isinstance(pagination, dict) else None
    try:
        return int(total) if total is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_user_summary(resource: Mapping[str, Any]) -> UserSummary:
    attrs = _attrs(resource)
    return UserSummary(
        id=str(resource.get("id", "")),
        full_name=attrs.get("full_name"),
        first_name=attrs.get("first_name"),
        last_name=attrs.get("last_name"),
        image_url=attrs.get("image_url"),
        thumb_url=attrs.get("thumb_url"),
        url=attrs.get("url"),
        vanity=attrs.get("vanity"),
        is_creator=attrs.get("is_creator"),
    )


def _normalize_tier(resource: Mapping[str, Any]) -> Tier:
    attrs = _attrs(resource)
    return Tier(
        id=str(resource.get("id", "")),
        title=attrs.get("title"),
        amount_cents=attrs.get("amount_cents"),
        description=attrs.get("description"),
        patron_count=attrs.get("patron_count"),
        post_count=attrs.get("post_count"),
        published=attrs.get("published"),
        published_at=attrs.get("published_at"),
        unpublished_at=attrs.get("unpublished_at"),
        requires_shipping=attrs.get("requires_shipping"),
        remaining=attrs.get("remaining"),
        user_limit=attrs.get("user_limit"),
        url=attrs.get("url"),
        image_url=attrs.get("image_url"),
        created_at=attrs.get("created_at"),
        edited_at=attrs.get("edited_at"),
    )


def _normalize_campaign(
    resource: Mapping[str, Any],
    index: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    allow_unscoped_includes: bool = False,
) -> Campaign:
    attrs = _attrs(resource)
    campaign_id = str(resource.get("id", ""))

    tiers: list[Tier] = []
    for tier_id in _relationship_ids(resource, "tiers"):
        tier = index.get(("tier", tier_id))
        tiers.append(_normalize_tier(tier) if tier is not None else Tier(id=tier_id))
    if not tiers and allow_unscoped_includes:
        tiers = [_normalize_tier(item) for item in index.values() if item.get("type") == "tier"]

    creator: UserSummary | None = None
    creator_ids = _relationship_ids(resource, "creator")
    if creator_ids:
        creator_resource = index.get(("user", creator_ids[0]))
        if creator_resource is not None:
            creator = _normalize_user_summary(creator_resource)
    if creator is None and allow_unscoped_includes:
        users = [item for item in index.values() if item.get("type") == "user"]
        if len(users) == 1:
            creator = _normalize_user_summary(users[0])

    return Campaign(
        id=campaign_id,
        name=attrs.get("name"),
        creation_name=attrs.get("creation_name"),
        summary=attrs.get("summary"),
        one_liner=attrs.get("one_liner"),
        currency=attrs.get("currency"),
        patron_count=attrs.get("patron_count"),
        is_monthly=attrs.get("is_monthly"),
        is_charged_immediately=attrs.get("is_charged_immediately"),
        is_nsfw=attrs.get("is_nsfw"),
        is_eligible_for_live=attrs.get("is_eligible_for_live"),
        show_earnings=attrs.get("show_earnings"),
        url=attrs.get("url"),
        pledge_url=attrs.get("pledge_url"),
        image_url=attrs.get("image_url"),
        image_small_url=attrs.get("image_small_url"),
        created_at=attrs.get("created_at"),
        published_at=attrs.get("published_at"),
        vanity=attrs.get("vanity"),
        pay_per_name=attrs.get("pay_per_name"),
        creator=creator,
        tiers=tiers,
    )


def _normalize_member(
    resource: Mapping[str, Any],
    index: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    allow_unscoped_includes: bool = False,
) -> Member:
    attrs = _attrs(resource)

    tier_ids = _relationship_ids(resource, "currently_entitled_tiers")
    tiers: list[Tier] = []
    for tier_id in tier_ids:
        tier = index.get(("tier", tier_id))
        tiers.append(_normalize_tier(tier) if tier is not None else Tier(id=tier_id))
    if not tiers and allow_unscoped_includes:
        tiers = [_normalize_tier(item) for item in index.values() if item.get("type") == "tier"]

    user: UserSummary | None = None
    user_ids = _relationship_ids(resource, "user")
    if user_ids:
        user_resource = index.get(("user", user_ids[0]))
        if user_resource is not None:
            user = _normalize_user_summary(user_resource)
    if user is None and allow_unscoped_includes:
        users = [item for item in index.values() if item.get("type") == "user"]
        if len(users) == 1:
            user = _normalize_user_summary(users[0])

    address: Address | None = None
    address_ids = _relationship_ids(resource, "address")
    if address_ids:
        address_resource = index.get(("address", address_ids[0]))
    elif allow_unscoped_includes:
        addresses = [item for item in index.values() if item.get("type") == "address"]
        address_resource = addresses[0] if len(addresses) == 1 else None
    else:
        address_resource = None
    if address_resource is not None:
        address_attrs = _attrs(address_resource)
        address = Address(
            id=str(address_resource.get("id", "")),
            addressee=address_attrs.get("addressee"),
            line_1=address_attrs.get("line_1"),
            line_2=address_attrs.get("line_2"),
            city=address_attrs.get("city"),
            state=address_attrs.get("state"),
            postal_code=address_attrs.get("postal_code"),
            country=address_attrs.get("country"),
            phone_number=address_attrs.get("phone_number"),
        )

    return Member(
        id=str(resource.get("id", "")),
        full_name=attrs.get("full_name"),
        email=attrs.get("email"),
        patron_status=attrs.get("patron_status"),
        pledge_cadence=attrs.get("pledge_cadence"),
        campaign_lifetime_support_cents=attrs.get("campaign_lifetime_support_cents"),
        currently_entitled_amount_cents=attrs.get("currently_entitled_amount_cents"),
        will_pay_amount_cents=attrs.get("will_pay_amount_cents"),
        last_charge_date=attrs.get("last_charge_date"),
        last_charge_status=attrs.get("last_charge_status"),
        next_charge_date=attrs.get("next_charge_date"),
        pledge_relationship_start=attrs.get("pledge_relationship_start"),
        is_free_trial=attrs.get("is_free_trial"),
        is_gifted=attrs.get("is_gifted"),
        note=attrs.get("note"),
        tiers=tiers,
        user=user,
        address=address,
    )


def _normalize_post(
    resource: Mapping[str, Any],
    index: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> Post:
    attrs = _attrs(resource)
    tier_ids: list[str] = []
    raw_tiers = attrs.get("tiers")
    if isinstance(raw_tiers, list):
        for item in raw_tiers:
            if isinstance(item, dict) and item.get("id") is not None:
                tier_ids.append(str(item["id"]))
            elif isinstance(item, str | int):
                tier_ids.append(str(item))
    access_rule_types: list[str] = []
    if index is not None:
        for rule_id in _relationship_ids(resource, "access_rules"):
            rule = index.get(("access-rule", rule_id))
            if rule is not None:
                rule_type = _attrs(rule).get("access_rule_type")
                if rule_type:
                    access_rule_types.append(str(rule_type))
    embed_data = attrs.get("embed_data")
    content = attrs.get("content")
    content_truncated = False
    if isinstance(content, str) and len(content) > MAX_POST_CONTENT_CHARS:
        content = content[:MAX_POST_CONTENT_CHARS]
        content_truncated = True
    return Post(
        id=str(resource.get("id", "")),
        title=attrs.get("title"),
        content=content,
        post_type=attrs.get("post_type"),
        is_public=attrs.get("is_public"),
        is_paid=attrs.get("is_paid"),
        is_draft=attrs.get("is_draft"),
        published_at=attrs.get("published_at"),
        scheduled_for=attrs.get("scheduled_for"),
        created_at=attrs.get("created_at"),
        edited_at=attrs.get("edited_at"),
        url=attrs.get("url"),
        patreon_url=attrs.get("patreon_url"),
        embed_url=attrs.get("embed_url"),
        embed_data=embed_data if isinstance(embed_data, dict) else None,
        app_status=attrs.get("app_status"),
        current_user_can_edit=attrs.get("current_user_can_edit"),
        content_truncated=content_truncated,
        tier_ids=tier_ids,
        access_rule_types=access_rule_types,
    )


def _normalize_webhook(resource: Mapping[str, Any]) -> Webhook:
    attrs = _attrs(resource)
    triggers = attrs.get("triggers")
    if isinstance(triggers, str):
        trigger_list = [item.strip() for item in triggers.split(",") if item.strip()]
    elif isinstance(triggers, list):
        trigger_list = [str(item) for item in triggers]
    else:
        trigger_list = []
    return Webhook(
        id=str(resource.get("id", "")),
        uri=attrs.get("uri"),
        triggers=trigger_list,
        paused=attrs.get("paused"),
        secret=attrs.get("secret"),
        last_attempted_at=attrs.get("last_attempted_at"),
        num_consecutive_times_failed=attrs.get("num_consecutive_times_failed"),
    )


def _normalize_live(resource: Mapping[str, Any]) -> Live:
    attrs = _attrs(resource)
    return Live(
        id=str(resource.get("id", "")),
        title=attrs.get("title"),
        description=attrs.get("description"),
        state=attrs.get("state"),
        scheduled_for=attrs.get("scheduled_for"),
        rtmp_url=attrs.get("rtmp_url"),
        stream_key=attrs.get("stream_key"),
    )


class PatreonAPI:
    """High-level operations used by the MCP tools."""

    def __init__(
        self,
        client: PatreonClient,
        *,
        webhook_allowed_hosts: Sequence[str] = (),
        allow_pii: bool = False,
    ) -> None:
        self.client = client
        self.webhook_allowed_hosts = tuple(webhook_allowed_hosts)
        self.allow_pii = allow_pii

    def _require_pii_allowed(
        self,
        *,
        include_email: bool,
        include_note: bool,
        include_address: bool,
    ) -> None:
        if (include_email or include_note or include_address) and not self.allow_pii:
            raise PatreonError(
                "Patron email, notes, and addresses are disabled by default. Set "
                "PATREON_ALLOW_PII=1 to enable these fields."
            )

    async def _resolve_campaign_id(self, campaign_id: str | None) -> str:
        if campaign_id:
            return _validate_id(campaign_id, "campaign_id")

        payload = await self.client.request(
            "GET",
            "campaigns",
            params={"page[count]": "100"},
        )
        campaigns = _resource_list(payload)
        if not campaigns:
            raise PatreonError(
                "No campaigns are visible to this token. Check that the token has "
                "the campaigns scope and belongs to a creator."
            )
        if len(campaigns) > 1:
            ids = ", ".join(str(item.get("id")) for item in campaigns)
            raise PatreonError(
                f"Multiple campaigns are visible ({ids}). Pass campaign_id explicitly."
            )
        campaign_id = campaigns[0].get("id")
        if campaign_id is None:
            raise PatreonError("Patreon returned a campaign without an id.")
        return _validate_id(str(campaign_id), "campaign_id")

    async def get_identity(
        self,
        *,
        include_campaign: bool = True,
        include_email: bool = False,
    ) -> User:
        user_fields = list(USER_FIELDS)
        if include_email:
            user_fields.append("email")
        params: dict[str, str] = {"fields[user]": _csv(user_fields)}
        if include_campaign:
            params["include"] = "campaign"
            params["fields[campaign]"] = _csv(CAMPAIGN_FIELDS)

        payload = await self.client.request("GET", "identity", params=params)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no identity data.")
        attrs = _attrs(data)
        index = _included_index(payload)

        campaign: Campaign | None = None
        if include_campaign:
            campaign_ids = _relationship_ids(data, "campaign")
            campaign_resource = index.get(("campaign", campaign_ids[0])) if campaign_ids else None
            if campaign_resource is None:
                candidates = [item for item in index.values() if item.get("type") == "campaign"]
                if len(candidates) == 1:
                    campaign_resource = candidates[0]
            if campaign_resource is not None:
                campaign = _normalize_campaign(
                    campaign_resource, index, allow_unscoped_includes=True
                )

        return User(
            id=str(data.get("id", "")),
            full_name=attrs.get("full_name"),
            first_name=attrs.get("first_name"),
            last_name=attrs.get("last_name"),
            image_url=attrs.get("image_url"),
            thumb_url=attrs.get("thumb_url"),
            url=attrs.get("url"),
            vanity=attrs.get("vanity"),
            is_creator=attrs.get("is_creator"),
            email=attrs.get("email"),
            about=attrs.get("about"),
            created=attrs.get("created"),
            campaign=campaign,
        )

    async def list_campaigns(
        self,
        *,
        include_tiers: bool = True,
        include_creator: bool = False,
    ) -> CampaignList:
        params: dict[str, str] = {"fields[campaign]": _csv(CAMPAIGN_FIELDS)}
        includes: list[str] = []
        if include_tiers:
            includes.append("tiers")
            params["fields[tier]"] = _csv(TIER_FIELDS)
        if include_creator:
            includes.append("creator")
            params["fields[user]"] = _csv(USER_FIELDS)
        if includes:
            params["include"] = ",".join(includes)

        payload = await self.client.request("GET", "campaigns", params=params)
        index = _included_index(payload)
        resources = _resource_list(payload)
        allow_unscoped = len(resources) == 1
        return CampaignList(
            campaigns=[
                _normalize_campaign(item, index, allow_unscoped_includes=allow_unscoped)
                for item in resources
            ],
            total=_total(payload),
        )

    async def get_campaign(
        self,
        campaign_id: str | None = None,
        *,
        include_tiers: bool = True,
        include_creator: bool = True,
    ) -> Campaign:
        resolved_id = await self._resolve_campaign_id(campaign_id)
        params: dict[str, str] = {"fields[campaign]": _csv(CAMPAIGN_FIELDS)}
        includes: list[str] = []
        if include_tiers:
            includes.append("tiers")
            params["fields[tier]"] = _csv(TIER_FIELDS)
        if include_creator:
            includes.append("creator")
            params["fields[user]"] = _csv(USER_FIELDS)
        if includes:
            params["include"] = ",".join(includes)

        payload = await self.client.request(
            "GET", f"campaigns/{quote(resolved_id, safe='')}", params=params
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no campaign data.")
        return _normalize_campaign(data, _included_index(payload), allow_unscoped_includes=True)

    async def list_members(
        self,
        campaign_id: str | None = None,
        *,
        cursor: str | None = None,
        page_size: int = 20,
        include_tiers: bool = True,
        include_user: bool = False,
        include_email: bool = False,
        include_note: bool = False,
        include_address: bool = False,
    ) -> MemberPage:
        _validate_page_size(page_size)
        self._require_pii_allowed(
            include_email=include_email,
            include_note=include_note,
            include_address=include_address,
        )
        resolved_id = await self._resolve_campaign_id(campaign_id)

        member_fields = list(MEMBER_FIELDS)
        if include_email:
            member_fields.append("email")
        if include_note:
            member_fields.append("note")

        params: dict[str, str] = {
            "fields[member]": _csv(member_fields),
            "page[count]": str(page_size),
        }
        includes: list[str] = []
        if include_tiers:
            includes.append("currently_entitled_tiers")
            params["fields[tier]"] = _csv(TIER_FIELDS)
        if include_user:
            includes.append("user")
            params["fields[user]"] = _csv(USER_FIELDS)
        if include_address:
            includes.append("address")
            params["fields[address]"] = _csv(ADDRESS_FIELDS)
        if includes:
            params["include"] = ",".join(includes)
        cursor = _validate_cursor(cursor)
        if cursor:
            params["page[cursor]"] = cursor

        payload = await self.client.request(
            "GET",
            f"campaigns/{quote(resolved_id, safe='')}/members",
            params=params,
        )
        index = _included_index(payload)
        resources = _resource_list(payload)
        allow_unscoped = len(resources) == 1
        return MemberPage(
            members=[
                _normalize_member(item, index, allow_unscoped_includes=allow_unscoped)
                for item in resources
            ],
            next_cursor=_next_cursor(payload),
            total=_total(payload),
        )

    async def get_member(
        self,
        member_id: str,
        *,
        include_tiers: bool = True,
        include_user: bool = False,
        include_email: bool = False,
        include_note: bool = False,
        include_address: bool = False,
    ) -> Member:
        member_id = _validate_id(member_id, "member_id")
        self._require_pii_allowed(
            include_email=include_email,
            include_note=include_note,
            include_address=include_address,
        )
        member_fields = list(MEMBER_FIELDS)
        if include_email:
            member_fields.append("email")
        if include_note:
            member_fields.append("note")

        params: dict[str, str] = {"fields[member]": _csv(member_fields)}
        includes: list[str] = []
        if include_tiers:
            includes.append("currently_entitled_tiers")
            params["fields[tier]"] = _csv(TIER_FIELDS)
        if include_user:
            includes.append("user")
            params["fields[user]"] = _csv(USER_FIELDS)
        if include_address:
            includes.append("address")
            params["fields[address]"] = _csv(ADDRESS_FIELDS)
        if includes:
            params["include"] = ",".join(includes)

        payload = await self.client.request(
            "GET", f"members/{quote(member_id, safe='')}", params=params
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no member data.")
        return _normalize_member(data, _included_index(payload), allow_unscoped_includes=True)

    async def list_posts(
        self,
        campaign_id: str | None = None,
        *,
        cursor: str | None = None,
        page_size: int = 20,
        include_content: bool = False,
    ) -> PostPage:
        _validate_page_size(page_size)
        if include_content and page_size > 100:
            raise PatreonError("page_size must be at most 100 when include_content=True.")
        resolved_id = await self._resolve_campaign_id(campaign_id)

        post_fields = list(POST_FIELDS)
        if include_content:
            post_fields.append("content")
        params: dict[str, str] = {
            "fields[post]": _csv(post_fields),
            "page[count]": str(page_size),
        }
        cursor = _validate_cursor(cursor)
        if cursor:
            params["page[cursor]"] = cursor

        payload = await self.client.request(
            "GET",
            f"campaigns/{quote(resolved_id, safe='')}/posts",
            params=params,
        )
        index = _included_index(payload)
        return PostPage(
            posts=[_normalize_post(item, index) for item in _resource_list(payload)],
            next_cursor=_next_cursor(payload),
            total=_total(payload),
        )

    async def get_post(self, post_id: str) -> Post:
        post_id = _validate_id(post_id, "post_id")
        post_fields = list(POST_FIELDS) + ["content"]
        payload = await self.client.request(
            "GET",
            f"posts/{quote(post_id, safe='')}",
            params={"fields[post]": _csv(post_fields)},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no post data.")
        return _normalize_post(data, _included_index(payload))

    async def list_webhooks(self, *, include_secrets: bool = False) -> WebhookList:
        payload = await self.client.request(
            "GET", "webhooks", params={"fields[webhook]": _csv(WEBHOOK_FIELDS)}
        )
        webhooks = [_normalize_webhook(item) for item in _resource_list(payload)]
        if not include_secrets:
            for webhook in webhooks:
                webhook.secret = None
        return WebhookList(webhooks=webhooks)

    async def create_webhook(
        self,
        uri: str,
        triggers: Sequence[str],
        campaign_id: str | None = None,
    ) -> Webhook:
        uri = _validate_webhook_uri(uri, self.webhook_allowed_hosts)
        trigger_list = _validate_triggers(triggers)
        resolved_id = await self._resolve_campaign_id(campaign_id)

        body = {
            "data": {
                "type": "webhook",
                "attributes": {"triggers": trigger_list, "uri": uri},
                "relationships": {"campaign": {"data": {"type": "campaign", "id": resolved_id}}},
            }
        }
        payload = await self.client.request("POST", "webhooks", json_body=body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no webhook data after creation.")
        return _normalize_webhook(data)

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        uri: str | None = None,
        triggers: Sequence[str] | None = None,
        paused: bool | None = None,
    ) -> Webhook:
        webhook_id = _validate_id(webhook_id, "webhook_id")
        attributes: dict[str, Any] = {}
        if uri is not None:
            attributes["uri"] = _validate_webhook_uri(uri, self.webhook_allowed_hosts)
        if triggers is not None:
            attributes["triggers"] = _validate_triggers(triggers)
        if paused is not None:
            if paused is False:
                if not self.webhook_allowed_hosts:
                    raise PatreonError(
                        "Unpausing webhooks is disabled without PATREON_WEBHOOK_ALLOWED_HOSTS."
                    )
                existing = await self.list_webhooks(include_secrets=False)
                match = next(
                    (item for item in existing.webhooks if item.id == webhook_id),
                    None,
                )
                if match is not None and match.uri:
                    _validate_webhook_uri(match.uri, self.webhook_allowed_hosts)
            attributes["paused"] = paused
        if not attributes:
            raise PatreonError(
                "Provide at least one of uri, triggers, or paused to update a webhook."
            )

        body = {
            "data": {
                "id": webhook_id,
                "type": "webhook",
                "attributes": attributes,
            }
        }
        payload = await self.client.request(
            "PATCH", f"webhooks/{quote(webhook_id, safe='')}", json_body=body
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no webhook data after update.")
        return _normalize_webhook(data)

    async def delete_webhook(self, webhook_id: str) -> DeleteResult:
        webhook_id = _validate_id(webhook_id, "webhook_id")
        await self.client.request("DELETE", f"webhooks/{quote(webhook_id, safe='')}")
        return DeleteResult(id=webhook_id)

    async def create_live(
        self,
        *,
        title: str,
        state: str,
        description: str | None = None,
        scheduled_for: str | None = None,
        live_access_rule_ids: Sequence[int] | None = None,
        include_stream_key: bool = False,
    ) -> Live:
        if not isinstance(title, str):
            raise PatreonError("title must be a string.")
        title = title.strip()
        if not title:
            raise PatreonError("title must not be empty.")
        if len(title) > MAX_LIVE_TITLE_CHARS:
            raise PatreonError(f"title must be at most {MAX_LIVE_TITLE_CHARS} characters.")
        if description is not None:
            if not isinstance(description, str):
                raise PatreonError("description must be a string.")
            if len(description) > MAX_LIVE_DESCRIPTION_CHARS:
                raise PatreonError(
                    f"description must be at most {MAX_LIVE_DESCRIPTION_CHARS} characters."
                )
        if state not in LIVE_CREATE_STATES:
            raise PatreonError("state must be 'pre_live' or 'live' when creating a live.")

        attributes: dict[str, Any] = {"title": title, "state": state}
        if description is not None:
            attributes["description"] = description
        if state == "pre_live":
            if not scheduled_for:
                raise PatreonError("scheduled_for is required when state is 'pre_live'.")
            attributes["scheduled_for"] = _normalize_scheduled_for(scheduled_for)
        elif scheduled_for is not None:
            raise PatreonError("scheduled_for is only valid when state is 'pre_live'.")

        if live_access_rule_ids is not None:
            if not live_access_rule_ids:
                raise PatreonError("live_access_rule_ids must not be empty when provided.")
            if len(live_access_rule_ids) > MAX_LIVE_ACCESS_RULES:
                raise PatreonError(
                    f"At most {MAX_LIVE_ACCESS_RULES} live_access_rule_ids are allowed."
                )
            if any(not isinstance(item, int) or item <= 0 for item in live_access_rule_ids):
                raise PatreonError("live_access_rule_ids must contain positive integers.")
            attributes["live_access_rule_ids"] = list(live_access_rule_ids)

        body = {"data": {"type": "live", "attributes": attributes}}
        payload = await self.client.request("POST", "lives", json_body=body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no live data after creation.")
        live = _normalize_live(data)
        if not include_stream_key:
            live.stream_key = None
        return live

    async def get_live(self, live_id: str, *, include_stream_key: bool = False) -> Live:
        live_id = _validate_id(live_id, "live_id")
        payload = await self.client.request(
            "GET",
            f"lives/{quote(live_id, safe='')}",
            params={"fields[live]": _csv(LIVE_FIELDS)},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no live data.")
        live = _normalize_live(data)
        if not include_stream_key:
            live.stream_key = None
        return live

    async def update_live(
        self, live_id: str, state: str, *, include_stream_key: bool = False
    ) -> Live:
        live_id = _validate_id(live_id, "live_id")
        if state not in LIVE_UPDATE_STATES:
            raise PatreonError("state must be 'live' or 'live_ended' when updating a live.")
        body = {
            "data": {
                "id": live_id,
                "type": "live",
                "attributes": {"state": state},
            }
        }
        payload = await self.client.request(
            "PATCH", f"lives/{quote(live_id, safe='')}", json_body=body
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no live data after update.")
        live = _normalize_live(data)
        if not include_stream_key:
            live.stream_key = None
        return live
