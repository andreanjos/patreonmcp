from __future__ import annotations

import json
import stat
from urllib.parse import parse_qs

import httpx2
import pytest

from patreonmcp.api import PatreonAPI
from patreonmcp.client import PatreonClient, PatreonError
from patreonmcp.config import Settings


async def no_sleep(_seconds: float) -> None:
    return None


def make_api(handler, settings: Settings | None = None) -> tuple[PatreonAPI, PatreonClient]:
    client = PatreonClient(
        settings or Settings(access_token="test-token"),
        transport=httpx2.MockTransport(handler),
        sleep=no_sleep,
    )
    return PatreonAPI(client), client


def json_response(payload: dict, status_code: int = 200) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload)


async def test_identity_normalizes_campaign() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/oauth2/v2/identity"
        params = request.url.params
        assert "email" not in params["fields[user]"].split(",")
        assert params["include"] == "campaign"
        return json_response(
            {
                "data": {
                    "type": "user",
                    "id": "1",
                    "attributes": {"full_name": "Creator", "is_creator": True},
                    "relationships": {"campaign": {"data": {"type": "campaign", "id": "42"}}},
                },
                "included": [
                    {
                        "type": "campaign",
                        "id": "42",
                        "attributes": {
                            "name": "My Campaign",
                            "patron_count": 12,
                            "currency": "USD",
                        },
                    }
                ],
            }
        )

    api, client = make_api(handler)
    user = await api.get_identity()
    await client.aclose()

    assert user.id == "1"
    assert user.full_name == "Creator"
    assert user.email is None
    assert user.campaign is not None
    assert user.campaign.id == "42"
    assert user.campaign.name == "My Campaign"
    assert user.campaign.patron_count == 12


async def test_list_members_pagination_tiers_and_optional_fields() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/oauth2/v2/campaigns/42/members"
        params = request.url.params
        assert params["page[count]"] == "2"
        assert params["page[cursor]"] == "cursor-1"
        assert "email" in params["fields[member]"].split(",")
        assert "note" in params["fields[member]"].split(",")
        assert "address" in params["include"].split(",")
        assert "user" in params["include"].split(",")
        return json_response(
            {
                "data": [
                    {
                        "type": "member",
                        "id": "member-1",
                        "attributes": {
                            "full_name": "Patron One",
                            "email": "one@example.com",
                            "note": "VIP",
                            "patron_status": "active_patron",
                            "campaign_lifetime_support_cents": 1200,
                            "currently_entitled_amount_cents": 500,
                        },
                        "relationships": {
                            "currently_entitled_tiers": {
                                "data": [{"type": "tier", "id": "tier-1"}]
                            },
                            "user": {"data": {"type": "user", "id": "user-1"}},
                            "address": {"data": {"type": "address", "id": "addr-1"}},
                        },
                    }
                ],
                "included": [
                    {
                        "type": "tier",
                        "id": "tier-1",
                        "attributes": {"title": "Gold", "amount_cents": 500},
                    },
                    {
                        "type": "user",
                        "id": "user-1",
                        "attributes": {"full_name": "Patron One", "url": "https://patreon.com/x"},
                    },
                    {
                        "type": "address",
                        "id": "addr-1",
                        "attributes": {"city": "Lisbon", "country": "PT"},
                    },
                ],
                "meta": {"pagination": {"cursors": {"next": "cursor-2"}, "total": 3}},
            }
        )

    api, client = make_api(handler)
    page = await api.list_members(
        "42",
        cursor="cursor-1",
        page_size=2,
        include_email=True,
        include_note=True,
        include_address=True,
        include_user=True,
    )
    await client.aclose()

    assert page.next_cursor == "cursor-2"
    assert page.total == 3
    assert len(page.members) == 1
    member = page.members[0]
    assert member.email == "one@example.com"
    assert member.note == "VIP"
    assert member.tiers[0].title == "Gold"
    assert member.user is not None and member.user.id == "user-1"
    assert member.address is not None and member.address.city == "Lisbon"


async def test_list_posts_omits_content_by_default() -> None:
    seen: list[dict[str, str]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/oauth2/v2/campaigns/42/posts"
        seen.append(dict(request.url.params))
        return json_response(
            {
                "data": [
                    {
                        "type": "post",
                        "id": "post-1",
                        "attributes": {
                            "title": "Hello",
                            "is_public": True,
                            "tiers": ["tier-1"],
                        },
                    }
                ],
                "meta": {"pagination": {"cursors": {"next": None}, "total": 1}},
            }
        )

    api, client = make_api(handler)
    page = await api.list_posts("42", page_size=5)
    await client.aclose()

    assert "content" not in seen[0]["fields[post]"].split(",")
    assert page.posts[0].title == "Hello"
    assert page.posts[0].tier_ids == ["tier-1"]
    assert page.next_cursor is None


async def test_get_post_requests_content() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/oauth2/v2/posts/post-1"
        assert "content" in request.url.params["fields[post]"].split(",")
        return json_response(
            {
                "data": {
                    "type": "post",
                    "id": "post-1",
                    "attributes": {
                        "title": "Hello",
                        "content": "<p>Body</p>",
                        "embed_data": {"provider": "youtube"},
                    },
                }
            }
        )

    api, client = make_api(handler)
    post = await api.get_post("post-1")
    await client.aclose()

    assert post.content == "<p>Body</p>"
    assert post.embed_data == {"provider": "youtube"}


async def test_webhook_crud_payloads() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "POST":
            return json_response(
                {
                    "data": {
                        "type": "webhook",
                        "id": "wh-1",
                        "attributes": {
                            "uri": "https://example.com/hook",
                            "triggers": ["members:create"],
                            "secret": "s3cret",
                            "paused": False,
                        },
                    }
                }
            )
        if request.method == "PATCH":
            return json_response(
                {
                    "data": {
                        "type": "webhook",
                        "id": "wh-1",
                        "attributes": {"paused": False, "uri": "https://example.com/hook"},
                    }
                }
            )
        if request.method == "DELETE":
            return httpx2.Response(204)
        return json_response({"data": []})

    api, client = make_api(handler)
    created = await api.create_webhook(
        "https://example.com/hook", ["members:create"], campaign_id="42"
    )
    updated = await api.update_webhook("wh-1", paused=False)
    deleted = await api.delete_webhook("wh-1")
    await client.aclose()

    assert created.secret == "s3cret"
    assert updated.paused is False
    assert deleted.id == "wh-1" and deleted.deleted

    post_body = calls[0][2]
    assert post_body["data"]["relationships"]["campaign"]["data"]["id"] == "42"
    assert calls[1][2]["data"]["attributes"] == {"paused": False}
    assert calls[2][0] == "DELETE"


async def test_webhook_validation_rejects_unsafe_input() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        raise AssertionError("no HTTP request expected")

    api, client = make_api(handler)
    with pytest.raises(PatreonError, match="https"):
        await api.create_webhook("http://example.com/hook", ["members:create"])
    with pytest.raises(PatreonError, match="Unknown webhook trigger"):
        await api.create_webhook("https://example.com/hook", ["members:nope"])
    with pytest.raises(PatreonError, match="at least one"):
        await api.update_webhook("wh-1")
    await client.aclose()


async def test_create_live_normalizes_schedule_and_rejects_bad_states() -> None:
    seen: dict = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        return json_response(
            {
                "data": {
                    "type": "live",
                    "id": "live-1",
                    "attributes": {
                        "title": "Launch",
                        "state": "pre_live",
                        "scheduled_for": "2026-10-20T16:00:00Z",
                    },
                }
            }
        )

    api, client = make_api(handler)
    live = await api.create_live(
        title="Launch",
        state="pre_live",
        scheduled_for="2026-10-20T18:00:00+02:00",
        live_access_rule_ids=[7],
    )
    await client.aclose()

    assert live.id == "live-1"
    assert seen["data"]["attributes"]["scheduled_for"] == "2026-10-20T16:00:00Z"
    assert seen["data"]["attributes"]["live_access_rule_ids"] == [7]

    api, client = make_api(handler)
    with pytest.raises(PatreonError, match="scheduled_for is required"):
        await api.create_live(title="Launch", state="pre_live")
    with pytest.raises(PatreonError, match="only valid"):
        await api.create_live(title="Launch", state="live", scheduled_for="2026-10-20T18:00:00Z")
    with pytest.raises(PatreonError, match="live_ended"):
        await api.update_live("live-1", "pre_live")
    await client.aclose()


async def test_refresh_on_401_persists_rotated_tokens(tmp_path) -> None:
    token_file = tmp_path / "tokens.json"
    token_calls: list[dict[str, list[str]]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/oauth2/token":
            token_calls.append(parse_qs(request.content.decode()))
            return json_response(
                {
                    "access_token": "new-token",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        if request.headers.get("authorization") == "Bearer old-token":
            return json_response(
                {"errors": [{"detail": "token expired", "code_name": "unauthorized"}]},
                status_code=401,
            )
        assert request.headers.get("authorization") == "Bearer new-token"
        return json_response(
            {
                "data": {
                    "type": "user",
                    "id": "1",
                    "attributes": {"full_name": "Creator"},
                }
            }
        )

    settings = Settings(
        access_token="old-token",
        refresh_token="old-refresh",
        client_id="client-id",
        client_secret="client-secret",
        token_file=token_file,
    )
    api, client = make_api(handler, settings)
    user = await api.get_identity(include_campaign=False)
    await client.aclose()

    assert user.full_name == "Creator"
    assert token_calls[0]["grant_type"] == ["refresh_token"]
    assert token_calls[0]["refresh_token"] == ["old-refresh"]
    assert token_calls[0]["client_id"] == ["client-id"]
    assert client.settings.access_token == "new-token"
    assert client.settings.refresh_token == "new-refresh"

    stored = json.loads(token_file.read_text())
    assert stored["access_token"] == "new-token"
    assert stored["refresh_token"] == "new-refresh"
    assert stored["expires_at"] is not None
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


async def test_refreshes_before_request_when_token_is_expired(tmp_path) -> None:
    calls: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/oauth2/token":
            return json_response(
                {
                    "access_token": "fresh",
                    "refresh_token": "r2",
                    "expires_in": 3600,
                }
            )
        assert request.headers["authorization"] == "Bearer fresh"
        return json_response(
            {
                "data": {
                    "type": "user",
                    "id": "1",
                    "attributes": {"full_name": "Creator"},
                }
            }
        )

    settings = Settings(
        access_token="expired",
        refresh_token="r1",
        client_id="c",
        client_secret="s",
        token_file=tmp_path / "tokens.json",
        expires_at=1.0,
    )
    api, client = make_api(handler, settings)
    await api.get_identity(include_campaign=False)
    await client.aclose()

    assert calls == ["/api/oauth2/token", "/api/oauth2/v2/identity"]


async def test_single_campaign_uses_included_tiers_without_relationship_data() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "data": [
                    {
                        "type": "campaign",
                        "id": "42",
                        "attributes": {"name": "Solo"},
                    }
                ],
                "included": [
                    {
                        "type": "tier",
                        "id": "t1",
                        "attributes": {"title": "Gold", "amount_cents": 500},
                    }
                ],
            }
        )

    api, client = make_api(handler)
    result = await api.list_campaigns(include_tiers=True)
    await client.aclose()

    assert result.campaigns[0].tiers[0].title == "Gold"


async def test_multiple_campaigns_do_not_cross_assign_included_tiers() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {
                "data": [
                    {"type": "campaign", "id": "1", "attributes": {"name": "One"}},
                    {"type": "campaign", "id": "2", "attributes": {"name": "Two"}},
                ],
                "included": [
                    {
                        "type": "tier",
                        "id": "t1",
                        "attributes": {"title": "Gold"},
                    }
                ],
            }
        )

    api, client = make_api(handler)
    result = await api.list_campaigns(include_tiers=True)
    await client.aclose()

    assert result.campaigns[0].tiers == []
    assert result.campaigns[1].tiers == []


async def test_campaign_resolution_uses_single_visible_campaign() -> None:
    calls: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/oauth2/v2/campaigns":
            return json_response({"data": [{"type": "campaign", "id": "42"}]})
        assert request.url.path == "/api/oauth2/v2/campaigns/42"
        return json_response(
            {"data": {"type": "campaign", "id": "42", "attributes": {"name": "Solo"}}}
        )

    api, client = make_api(handler)
    campaign = await api.get_campaign(include_tiers=False, include_creator=False)
    await client.aclose()

    assert campaign.name == "Solo"
    assert calls == ["/api/oauth2/v2/campaigns", "/api/oauth2/v2/campaigns/42"]


async def test_campaign_resolution_rejects_ambiguous_results() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        return json_response(
            {
                "data": [
                    {"type": "campaign", "id": "1"},
                    {"type": "campaign", "id": "2"},
                ]
            }
        )

    api, client = make_api(handler)
    with pytest.raises(PatreonError, match="Multiple campaigns"):
        await api.get_campaign()
    await client.aclose()
