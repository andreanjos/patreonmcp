"""Typed, model-facing representations of Patreon API v2 resources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PatreonModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Address(PatreonModel):
    id: str | None = None
    addressee: str | None = None
    line_1: str | None = None
    line_2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone_number: str | None = None


class UserSummary(PatreonModel):
    id: str
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    image_url: str | None = None
    thumb_url: str | None = None
    url: str | None = None
    vanity: str | None = None
    is_creator: bool | None = None


class Tier(PatreonModel):
    id: str
    title: str | None = None
    amount_cents: int | None = None
    description: str | None = None
    patron_count: int | None = None
    post_count: int | None = None
    published: bool | None = None
    published_at: str | None = None
    unpublished_at: str | None = None
    requires_shipping: bool | None = None
    remaining: int | None = None
    user_limit: int | None = None
    url: str | None = None
    image_url: str | None = None
    created_at: str | None = None
    edited_at: str | None = None


class Campaign(PatreonModel):
    id: str
    name: str | None = None
    creation_name: str | None = None
    summary: str | None = None
    one_liner: str | None = None
    currency: str | None = None
    patron_count: int | None = None
    is_monthly: bool | None = None
    is_charged_immediately: bool | None = None
    is_nsfw: bool | None = None
    is_eligible_for_live: bool | None = None
    show_earnings: bool | None = None
    url: str | None = None
    pledge_url: str | None = None
    image_url: str | None = None
    image_small_url: str | None = None
    created_at: str | None = None
    published_at: str | None = None
    vanity: str | None = None
    pay_per_name: str | None = None
    creator: UserSummary | None = None
    tiers: list[Tier] = Field(default_factory=list)


class User(UserSummary):
    email: str | None = None
    about: str | None = None
    created: str | None = None
    campaign: Campaign | None = None


class Member(PatreonModel):
    id: str
    full_name: str | None = None
    email: str | None = None
    patron_status: str | None = None
    pledge_cadence: int | None = None
    campaign_lifetime_support_cents: int | None = None
    currently_entitled_amount_cents: int | None = None
    will_pay_amount_cents: int | None = None
    last_charge_date: str | None = None
    last_charge_status: str | None = None
    next_charge_date: str | None = None
    pledge_relationship_start: str | None = None
    is_free_trial: bool | None = None
    is_gifted: bool | None = None
    note: str | None = None
    tiers: list[Tier] = Field(default_factory=list)
    user: UserSummary | None = None
    address: Address | None = None


class Post(PatreonModel):
    id: str
    title: str | None = None
    content: str | None = None
    post_type: str | None = None
    is_public: bool | None = None
    is_paid: bool | None = None
    is_draft: bool | None = None
    published_at: str | None = None
    scheduled_for: str | None = None
    created_at: str | None = None
    edited_at: str | None = None
    url: str | None = None
    patreon_url: str | None = None
    embed_url: str | None = None
    embed_data: dict | None = None
    app_status: str | None = None
    current_user_can_edit: bool | None = None
    content_truncated: bool = False
    tier_ids: list[str] = Field(default_factory=list)
    access_rule_types: list[str] = Field(default_factory=list)


class Webhook(PatreonModel):
    id: str
    uri: str | None = None
    triggers: list[str] = Field(default_factory=list)
    paused: bool | None = None
    secret: str | None = None
    last_attempted_at: str | None = None
    num_consecutive_times_failed: int | None = None


class Live(PatreonModel):
    id: str
    title: str | None = None
    description: str | None = None
    state: str | None = None
    scheduled_for: str | None = None
    rtmp_url: str | None = None
    stream_key: str | None = None


class CampaignList(PatreonModel):
    campaigns: list[Campaign] = Field(default_factory=list)
    total: int | None = None


class MemberPage(PatreonModel):
    members: list[Member] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None


class PostPage(PatreonModel):
    posts: list[Post] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int | None = None


class WebhookList(PatreonModel):
    webhooks: list[Webhook] = Field(default_factory=list)


class DeleteResult(PatreonModel):
    id: str
    deleted: bool = True
