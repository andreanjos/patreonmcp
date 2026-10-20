from __future__ import annotations

import json

import httpx2
import pytest

from patreonmcp.client import PatreonAuthError, PatreonError
from patreonmcp.config import Settings
from patreonmcp.web_api import PatreonWebAPI, PatreonWebClient


async def no_sleep(_seconds: float) -> None:
    return None


def make_web(handler) -> tuple[PatreonWebAPI, PatreonWebClient]:
    settings = Settings(session_cookie="session_id=abc; a_csrf=def")
    client = PatreonWebClient(
        settings,
        transport=httpx2.MockTransport(handler),
        sleep=no_sleep,
    )
    return PatreonWebAPI(client), client


def json_response(
    payload: dict, status_code: int = 200, headers: dict[str, str] | None = None
) -> httpx2.Response:
    return httpx2.Response(status_code, json=payload, headers=headers)


POST_DATA = {
    "type": "post",
    "id": "p1",
    "attributes": {
        "title": "Hello",
        "content": "Body",
        "post_type": "text_only",
        "published_at": "2026-10-20T12:00:00Z",
    },
}


async def test_create_post_uses_csrf_and_jsonapi_payload() -> None:
    calls: list[tuple[str, str, dict | None, str | None, str | None]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        calls.append(
            (
                request.method,
                request.url.path,
                body,
                request.headers.get("x-csrf-signature"),
                request.headers.get("cookie"),
            )
        )
        if request.url.path == "/REST/auth/CSRFTicket":
            assert request.url.params["uri"] in {"/posts/new", "/posts/p1/edit"}
            return json_response(
                {"token": "csrf-token", "time": "", "URI": ""},
                headers={"set-cookie": "a_csrf=server-token; Domain=www.patreon.com; Path=/"},
            )
        if request.url.path == "/api/posts/new":
            assert request.headers["content-type"] == "application/vnd.api+json"
            assert request.url.params["json-api-version"] == "1.0"
            return json_response({"data": {"type": "post", "id": "p1"}})
        if request.url.path == "/api/posts/p1" and request.method == "PATCH":
            assert request.headers["content-type"] == "application/vnd.api+json"
            assert body["data"]["attributes"]["tags"]["publish"] is True
            return json_response({"data": POST_DATA})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    api, client = make_web(handler)
    post = await api.create_post(
        title="Hello",
        content="Body",
        visibility="public",
        publish=True,
        tags=["release"],
    )
    await client.aclose()

    assert post.id == "p1"
    assert post.title == "Hello"
    csrf = calls[0]
    assert csrf[1] == "/REST/auth/CSRFTicket"
    assert csrf[3] is None
    create = next(call for call in calls if call[1] == "/api/posts/new")
    assert create[0] == "POST"
    assert create[2] == {
        "data": {
            "type": "post",
            "attributes": {"post_type": "text_only", "post_visibility": "public"},
        }
    }
    assert create[3] == "csrf-token"
    assert "session_id=abc" in (create[4] or "")
    assert "a_csrf=server-token" in (create[4] or "")

    patch = next(call for call in calls if call[1] == "/api/posts/p1")
    assert patch[0] == "PATCH"
    assert patch[3] == "csrf-token"
    payload = patch[2]
    assert payload["data"]["attributes"]["title"] == "Hello"
    assert payload["data"]["attributes"]["content"] == "Body"
    assert payload["data"]["attributes"]["post_visibility"] == "public"
    assert payload["data"]["attributes"]["tags"] == {"publish": True}
    assert payload["data"]["meta"] == {"auto_save": False}
    assert payload["data"]["relationships"]["user_defined_tags"]["data"] == [
        {"type": "post_tag", "id": "user_defined;release"}
    ]
    assert payload["included"] == [
        {
            "type": "post_tag",
            "id": "user_defined;release",
            "attributes": {"value": "release", "cardinality": 1},
        }
    ]


async def test_tier_visibility_resolves_campaign_access_rules() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/posts/new":
            assert request.headers["content-type"] == "application/vnd.api+json"
            assert request.url.params["json-api-version"] == "1.0"
            return json_response({"data": {"type": "post", "id": "p1"}})
        if request.url.path == "/api/campaigns/42":
            return json_response(
                {
                    "data": {
                        "type": "campaign",
                        "id": "42",
                        "relationships": {
                            "access_rules": {"data": [{"type": "access-rule", "id": "ar1"}]}
                        },
                    },
                    "included": [
                        {
                            "type": "access-rule",
                            "id": "ar1",
                            "attributes": {"access_rule_type": "tier"},
                            "relationships": {"tier": {"data": {"type": "reward", "id": "123"}}},
                        }
                    ],
                }
            )
        if request.url.path == "/api/posts/p1" and request.method == "PATCH":
            body = json.loads(request.content)
            assert body["data"]["attributes"]["post_visibility"] == "paid"
            assert body["data"]["relationships"]["access_rules"]["data"] == [
                {"type": "access-rule", "id": "ar1"}
            ]
            return json_response({"data": POST_DATA})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    api, client = make_web(handler)
    post = await api.create_post(
        title="Hello",
        content="Body",
        visibility="tiers",
        tier_ids=["123"],
        campaign_id="42",
        publish=True,
    )
    await client.aclose()

    assert post.id == "p1"


async def test_delete_post_and_list_drafts() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/posts/p1" and request.method == "DELETE":
            return httpx2.Response(204)
        if request.url.path == "/api/current_user":
            return json_response(
                {
                    "data": {
                        "type": "user",
                        "id": "u1",
                        "relationships": {"campaign": {"data": {"type": "campaign", "id": "42"}}},
                    }
                }
            )
        if request.url.path == "/api/posts":
            assert request.url.params["filter[is_draft]"] == "true"
            assert request.url.params["filter[campaign_id]"] == "42"
            assert request.url.params["page[count]"] == "5"
            assert "content" not in request.url.params["fields[post]"].split(",")
            return json_response(
                {
                    "data": [
                        {
                            "type": "post",
                            "id": "p2",
                            "attributes": {
                                "title": "Draft",
                                "post_type": "text_only",
                                "scheduled_for": "2026-10-21T12:00:00Z",
                            },
                        }
                    ],
                    "meta": {
                        "pagination": {
                            "total": 1,
                            "cursors": {"next": "next-page"},
                        }
                    },
                }
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    api, client = make_web(handler)
    deleted = await api.delete_post("p1")
    page = await api.list_drafts(page_size=5)
    await client.aclose()

    assert deleted.id == "p1" and deleted.deleted
    assert page.posts[0].title == "Draft"
    assert page.posts[0].scheduled_for == "2026-10-21T12:00:00Z"
    assert page.next_cursor == "next-page"
    assert page.total == 1


async def test_web_session_errors_are_actionable() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/posts/p1"
        return json_response(
            {
                "errors": [
                    {
                        "detail": "This route is restricted to logged in users.",
                        "code_name": "LoginRequired",
                    }
                ]
            },
            status_code=401,
        )

    api, client = make_web(handler)
    with pytest.raises(PatreonAuthError) as exc_info:
        await api.get_post("p1")
    await client.aclose()

    message = str(exc_info.value)
    assert "web session is not authenticated" in message
    assert "PATREON_SESSION_COOKIE" in message


def test_web_client_requires_session_cookie() -> None:
    with pytest.raises(PatreonAuthError, match="PATREON_SESSION_COOKIE"):
        PatreonWebClient(Settings())


async def test_partial_update_does_not_clear_relationships_or_publish() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/posts/p1" and request.method == "PATCH":
            body = json.loads(request.content)
            assert body["data"]["attributes"] == {"title": "New title"}
            assert "relationships" not in body["data"]
            return json_response(
                {
                    "data": {
                        "type": "post",
                        "id": "p1",
                        "attributes": {"title": "New title", "post_type": "text_only"},
                    }
                }
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    api, client = make_web(handler)
    post = await api.update_post("p1", title="New title")
    await client.aclose()

    assert post.title == "New title"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"title": "x" * 501}, "title must be at most"),
        ({"title": "ok", "content": "x" * 1_000_001}, "content must be at most"),
        ({"title": "ok", "tags": "one-tag"}, "tags must be a list"),
        (
            {"title": "ok", "visibility": "tiers", "tier_ids": "123"},
            "tier_ids must be a list",
        ),
    ],
)
async def test_create_post_rejects_oversized_or_mistyped_input(kwargs, message) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        raise AssertionError("validation must happen before any HTTP request")

    api, client = make_web(handler)
    with pytest.raises(Exception, match=message):
        await api.create_post(**kwargs)
    await client.aclose()


async def test_check_csrf_requires_session_bound_ticket() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/REST/auth/CSRFTicket"
        return json_response(
            {"token": "csrf-token", "time": "2026-10-20T00:00:00Z", "URI": "/posts/new"}
        )

    _, client = make_web(handler)
    await client.check_csrf()
    await client.aclose()


async def test_check_csrf_rejects_anonymous_ticket() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response({"token": "csrf-token", "time": "", "URI": ""})

    _, client = make_web(handler)
    with pytest.raises(PatreonAuthError, match="anonymous CSRF"):
        await client.check_csrf()
    await client.aclose()


async def test_publish_now_clears_previous_schedule() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/posts/p1" and request.method == "PATCH":
            body = json.loads(request.content)
            assert body["data"]["attributes"]["scheduled_for"] is None
            assert body["data"]["attributes"]["tags"] == {"publish": True}
            return json_response(
                {
                    "data": {
                        "type": "post",
                        "id": "p1",
                        "attributes": {"title": "T", "post_type": "text_only"},
                    }
                }
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    api, client = make_web(handler)
    await api.update_post("p1", publish=True)
    await client.aclose()


async def test_create_post_escapes_html_by_default() -> None:
    seen: dict = {}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/posts/new":
            return json_response({"data": {"type": "post", "id": "p1"}})
        if request.url.path == "/api/posts/p1" and request.method == "PATCH":
            seen.update(json.loads(request.content))
            return json_response(
                {
                    "data": {
                        "type": "post",
                        "id": "p1",
                        "attributes": {"title": "T", "post_type": "text_only"},
                    }
                }
            )
        raise AssertionError(f"unexpected request {request.url}")

    api, client = make_web(handler)
    await api.create_post(title="T", content="<b>hi</b>")
    await client.aclose()

    assert seen["data"]["attributes"]["content"] == "&lt;b&gt;hi&lt;/b&gt;"
    assert "<b>" in seen["data"]["attributes"]["content_json_string"]


async def test_unknown_tier_ids_fail_before_creating_a_draft() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return json_response({"token": "csrf-token", "time": "", "URI": ""})
        if request.url.path == "/api/campaigns/42":
            return json_response(
                {
                    "data": {"type": "campaign", "id": "42"},
                    "included": [
                        {
                            "type": "access-rule",
                            "id": "ar1",
                            "attributes": {"access_rule_type": "tier"},
                            "relationships": {"tier": {"data": {"type": "reward", "id": "111"}}},
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected request {request.url}")

    api, client = make_web(handler)
    with pytest.raises(PatreonError, match="999"):
        await api.create_post(
            title="T",
            content="C",
            visibility="tiers",
            tier_ids=["111", "999"],
            campaign_id="42",
        )
    await client.aclose()


async def test_web_error_description_is_surfaced() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return json_response(
            {"error": "invalid_request", "error_description": "missing post"},
            status_code=400,
        )

    api, client = make_web(handler)
    with pytest.raises(PatreonError, match="missing post") as exc_info:
        await api.get_post("p1")
    await client.aclose()
    assert "invalid_request" in str(exc_info.value)
