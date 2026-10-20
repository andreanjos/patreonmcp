from __future__ import annotations

import httpx2
import pytest
from mcp.client import Client

from patreonmcp.api import PatreonAPI
from patreonmcp.client import PatreonClient
from patreonmcp.config import ConfigError, Settings
from patreonmcp.server import create_server


async def test_mcp_tools_are_registered_and_callable() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/oauth2/v2/identity"
        return httpx2.Response(
            200,
            json={
                "data": {
                    "type": "user",
                    "id": "1",
                    "attributes": {"full_name": "Creator", "is_creator": True},
                }
            },
        )

    settings = Settings(access_token="test-token")
    client = PatreonClient(settings, transport=httpx2.MockTransport(handler))
    server = create_server(settings, api=PatreonAPI(client))

    async with Client(server) as mcp_client:
        tools = await mcp_client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "get_identity",
            "list_campaigns",
            "get_campaign",
            "list_members",
            "get_member",
            "list_posts",
            "get_post",
            "create_post",
            "update_post",
            "publish_post",
            "delete_post",
            "get_managed_post",
            "list_drafts",
            "list_webhooks",
            "create_webhook",
            "update_webhook",
            "delete_webhook",
            "create_live",
            "get_live",
            "update_live",
        }

        read_tool = next(tool for tool in tools.tools if tool.name == "list_members")
        assert read_tool.annotations is not None
        assert read_tool.annotations.read_only_hint is True
        assert "campaign_id" in read_tool.input_schema["properties"]

        result = await mcp_client.call_tool("get_identity", {"include_campaign": False})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["id"] == "1"
        assert result.structured_content["full_name"] == "Creator"

        invalid = await mcp_client.call_tool("update_webhook", {"webhook_id": "wh-1"})
        assert invalid.is_error
        assert invalid.content
        assert "Provide at least one" in invalid.content[0].text

        no_session = await mcp_client.call_tool(
            "create_post", {"title": "Draft", "content": "Hello"}
        )
        assert no_session.is_error
        assert no_session.content
        assert "PATREON_SESSION_COOKIE" in no_session.content[0].text

    await client.aclose()


async def test_tool_validation_errors_are_reported() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        raise AssertionError("no HTTP request expected")

    settings = Settings(access_token="test-token")
    client = PatreonClient(settings, transport=httpx2.MockTransport(handler))
    server = create_server(settings, api=PatreonAPI(client))

    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool("create_live", {"title": "Launch", "state": "pre_live"})
        assert result.is_error
        assert result.content
        assert "scheduled_for is required" in result.content[0].text

    await client.aclose()


def test_create_server_requires_credentials() -> None:
    with pytest.raises(ConfigError):
        create_server(Settings())


async def test_mcp_create_post_with_web_session() -> None:
    async def api_handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("official API should not be called")

    async def web_handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/REST/auth/CSRFTicket":
            return httpx2.Response(200, json={"token": "csrf", "time": "", "URI": ""})
        if request.url.path == "/api/posts/new":
            return httpx2.Response(200, json={"data": {"type": "post", "id": "p1"}})
        if request.url.path == "/api/posts/p1":
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "type": "post",
                        "id": "p1",
                        "attributes": {
                            "title": "MCP post",
                            "content": "Hello",
                            "post_type": "text_only",
                        },
                    }
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    from patreonmcp.web_api import PatreonWebAPI, PatreonWebClient

    settings = Settings(session_cookie="session_id=abc")
    api_client = PatreonClient(settings, transport=httpx2.MockTransport(api_handler))
    web_client = PatreonWebClient(settings, transport=httpx2.MockTransport(web_handler))
    server = create_server(
        settings,
        api=PatreonAPI(api_client),
        web_api=PatreonWebAPI(web_client),
    )

    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool(
            "create_post",
            {
                "title": "MCP post",
                "content": "Hello",
                "visibility": "public",
                "publish": True,
            },
        )
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["id"] == "p1"
        assert result.structured_content["title"] == "MCP post"

    await api_client.aclose()
    await web_client.aclose()
