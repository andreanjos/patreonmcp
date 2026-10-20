"""Patreon's undocumented browser API for creating and editing posts.

The official OAuth API v2 is read-only for posts. Patreon's own web editor uses
JSON:API routes under ``/api`` with a logged-in browser session cookie and a
CSRF ticket. Those routes are what this module speaks to.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx2

from .api import (
    _csv,
    _included_index,
    _next_cursor,
    _normalize_post,
    _normalize_scheduled_for,
    _resource_list,
    _total,
    _validate_id,
    _validate_page_size,
)
from .client import PatreonAuthError, PatreonError
from .config import Settings
from .models import DeleteResult, Post, PostPage

WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/155.0.0.0 Safari/537.36"
)
CSRF_PATH = "/REST/auth/CSRFTicket"
CSRF_HEADER = "X-CSRF-Signature"
CSRF_TTL_SECONDS = 300.0
MAX_RETRY_WAIT_SECONDS = 5.0

POST_VISIBILITIES = frozenset({"public", "paid", "tiers"})
POST_TYPES = frozenset({"text_only"})


def _plain_text_document(text: str) -> dict[str, Any]:
    if not text:
        return {"type": "doc", "content": []}
    nodes: list[dict[str, Any]] = []
    parts = text.split("\n")
    for index, part in enumerate(parts):
        if part:
            nodes.append({"type": "text", "text": part})
        if index < len(parts) - 1:
            nodes.append({"type": "hardBreak"})
    return {"type": "doc", "content": [{"type": "paragraph", "content": nodes}]}


def _parse_cookie_header(raw: str, domain: str) -> httpx2.Cookies:
    header = raw.strip()
    if header.lower().startswith("cookie:"):
        header = header[7:].strip()
    cookies = httpx2.Cookies()
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name or name.startswith("$"):
            continue
        cookies.set(
            name,
            value.strip().strip('"'),
            domain=domain,
            path="/",
        )
    if not cookies:
        raise PatreonError("PATREON_SESSION_COOKIE did not contain any name=value pairs.")
    return cookies


def _jsonapi_params(fields: Mapping[str, str] | None = None) -> dict[str, str]:
    params: dict[str, str] = {
        "json-api-version": "1.0",
        "json-api-use-default-includes": "false",
    }
    if fields:
        params["json-api-use-default-fields"] = "false"
        params.update(fields)
    return params


def _payload_errors(payload: Any) -> tuple[str | None, str | None, float | None]:
    if not isinstance(payload, dict):
        return None, None, None
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        return None, None, None
    first = errors[0] if isinstance(errors[0], dict) else {}
    detail = first.get("detail") or first.get("title")
    code = first.get("code_name") or first.get("code")
    retry = first.get("retry_after_seconds")
    try:
        retry_after = float(retry) if retry is not None else None
    except (TypeError, ValueError):
        retry_after = None
    return (
        str(detail) if detail else None,
        str(code) if code else None,
        retry_after,
    )


class PatreonWebClient:
    """Session-cookie client for Patreon's internal JSON:API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not settings.session_cookie:
            raise PatreonAuthError(
                "No Patreon web session is configured. Set PATREON_SESSION_COOKIE "
                "or PATREON_SESSION_COOKIE_FILE."
            )
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._csrf_token: str | None = None
        self._csrf_uri: str | None = None
        self._csrf_at = 0.0
        web_host = urlparse(settings.web_base_url).hostname or "www.patreon.com"
        self._http = httpx2.AsyncClient(
            base_url=settings.web_base_url.rstrip("/"),
            cookies=_parse_cookie_header(settings.session_cookie, web_host),
            timeout=settings.timeout_seconds,
            transport=transport,
            headers={
                "Accept": "application/vnd.api+json",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": settings.web_user_agent or WEB_USER_AGENT,
                "Origin": settings.web_base_url.rstrip("/"),
                "Referer": settings.web_base_url.rstrip("/") + "/",
            },
        )

    async def __aenter__(self) -> PatreonWebClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _csrf(self, uri: str) -> str:
        now = self._clock()
        if self._csrf_token and self._csrf_uri == uri and now - self._csrf_at < CSRF_TTL_SECONDS:
            return self._csrf_token
        try:
            response = await self._http.get(
                CSRF_PATH,
                params={"uri": uri},
                headers={"Accept": "application/json"},
            )
        except httpx2.HTTPError as exc:
            raise PatreonError(
                f"Could not reach Patreon's CSRF endpoint ({type(exc).__name__}): {exc}"
            ) from exc

        payload = self._maybe_json(response)
        token = payload.get("token") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not token:
            detail, code, _ = _payload_errors(payload)
            raise PatreonAuthError(
                detail
                or "Patreon did not issue a CSRF ticket. The web session may be "
                "missing or expired.",
                status_code=response.status_code,
                code=code or "csrf_failed",
            )
        self._csrf_token = str(token)
        self._csrf_uri = uri
        self._csrf_at = now
        return self._csrf_token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        csrf_uri: str = "/posts/new",
    ) -> dict[str, Any]:
        if path.startswith("/api/"):
            query = dict(params or {})
            query.setdefault("json-api-version", "1.0")
            query.setdefault("json-api-use-default-includes", "false")
            query.setdefault("json-api-use-default-fields", "false")
            params = query

        needs_csrf = method.upper() not in {"GET", "HEAD", "OPTIONS"}
        csrf_token = await self._csrf(csrf_uri) if needs_csrf else None
        headers = {
            "Referer": self.settings.web_base_url.rstrip("/") + csrf_uri,
        }
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers["Content-Type"] = "application/vnd.api+json"
        if csrf_token:
            headers[CSRF_HEADER] = csrf_token

        retries = 0
        csrf_retried = False
        while True:
            try:
                response = await self._http.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
            except httpx2.HTTPError as exc:
                raise PatreonError(
                    f"Could not reach Patreon ({type(exc).__name__}): {exc}"
                ) from exc

            if response.status_code == 403 and needs_csrf and not csrf_retried:
                self._csrf_token = None
                headers[CSRF_HEADER] = await self._csrf(csrf_uri)
                csrf_retried = True
                continue

            if response.status_code == 429:
                retry_after = self._retry_after(response)
                if (
                    retries == 0
                    and retry_after is not None
                    and retry_after <= MAX_RETRY_WAIT_SECONDS
                ):
                    retries += 1
                    await self._sleep(retry_after)
                    continue
                self._raise(response)

            if response.status_code in (502, 503, 504) and retries == 0:
                retries += 1
                await self._sleep(0.5)
                continue

            if response.status_code >= 400:
                self._raise(response)

            if response.status_code == 204 or not response.content:
                return {}
            payload = self._maybe_json(response)
            if not isinstance(payload, dict):
                raise PatreonError(
                    "Patreon's web API returned a response that was not JSON. "
                    "The session may have hit a Cloudflare challenge.",
                    status_code=response.status_code,
                )
            return payload

    def _retry_after(self, response: httpx2.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                pass
        _, _, retry = _payload_errors(self._maybe_json(response))
        return retry

    def _raise(self, response: httpx2.Response) -> None:
        payload = self._maybe_json(response)
        detail, code, retry_after = _payload_errors(payload)
        if response.status_code == 401:
            raise PatreonAuthError(
                "Patreon's web session is not authenticated or has expired. Copy a "
                "fresh PATREON_SESSION_COOKIE from a logged-in browser session.",
                status_code=401,
                code=code or "web_login_required",
            )
        if response.status_code == 403:
            raise PatreonAuthError(
                detail
                or "Patreon rejected the web request (CSRF or permission). Refresh "
                "PATREON_SESSION_COOKIE and retry.",
                status_code=403,
                code=code or "web_forbidden",
            )
        raise PatreonError(
            detail or f"Patreon's web API returned HTTP {response.status_code}.",
            status_code=response.status_code,
            code=code,
            retry_after=retry_after,
        )

    @staticmethod
    def _maybe_json(response: httpx2.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


class PatreonWebAPI:
    """Post-management operations exposed by Patreon's browser API."""

    POST_FIELDS = (
        "title",
        "content",
        "post_type",
        "published_at",
        "scheduled_for",
        "created_at",
        "edited_at",
        "url",
        "patreon_url",
        "current_user_can_edit",
    )

    def __init__(self, client: PatreonWebClient) -> None:
        self.client = client

    async def get_current_campaign_id(self) -> str:
        """Return the campaign attached to the authenticated web session."""

        return await self._campaign_id(None)

    async def _campaign_id(self, campaign_id: str | None) -> str:
        if campaign_id:
            return _validate_id(campaign_id, "campaign_id")
        payload = await self.client.request(
            "GET",
            "/api/current_user",
            params=_jsonapi_params(
                {
                    "include": "campaign",
                    "fields[campaign]": "name,vanity,url",
                }
            ),
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no current user data.")
        relationships = data.get("relationships")
        campaign = relationships.get("campaign") if isinstance(relationships, dict) else None
        relation_data = campaign.get("data") if isinstance(campaign, dict) else None
        if isinstance(relation_data, dict) and relation_data.get("id") is not None:
            return _validate_id(str(relation_data["id"]), "campaign_id")
        raise PatreonError(
            "Could not determine the current campaign from the web session. "
            "Pass campaign_id explicitly."
        )

    async def _access_rule_ids(self, campaign_id: str, tier_ids: Sequence[str]) -> list[str]:
        if not tier_ids:
            raise PatreonError("tier_ids is required when visibility is 'tiers'.")
        payload = await self.client.request(
            "GET",
            f"/api/campaigns/{campaign_id}",
            params=_jsonapi_params(
                {
                    "include": "access_rules,access_rules.tier.null",
                    "fields[access-rule]": "access_rule_type",
                    "fields[reward]": "title,amount_cents,is_free_tier",
                }
            ),
        )
        index = _included_index(payload)
        wanted = {str(item) for item in tier_ids}
        found: list[str] = []
        for rule in index.values():
            if rule.get("type") != "access-rule":
                continue
            relationships = rule.get("relationships")
            tier_rel = relationships.get("tier") if isinstance(relationships, dict) else None
            tier_data = tier_rel.get("data") if isinstance(tier_rel, dict) else None
            if isinstance(tier_data, dict) and str(tier_data.get("id")) in wanted:
                found.append(str(rule.get("id")))
        if not found:
            raise PatreonError(
                "None of the requested tier_ids matched an access rule on the "
                "current campaign. Check the tier IDs."
            )
        return list(dict.fromkeys(found))

    async def _build_post_payload(
        self,
        *,
        title: str | None = None,
        content: str | None = None,
        teaser_text: str | None = None,
        visibility: str | None = None,
        tier_ids: Sequence[str] | None = None,
        publish: bool | None = None,
        scheduled_for: str | None = None,
        tags: Sequence[str] | None = None,
        campaign_id: str | None = None,
        post_type: str | None = None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        if title is not None:
            title = title.strip()
            if not title:
                raise PatreonError("title must not be empty.")
            attributes["title"] = title
        if content is not None:
            normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
            attributes["content"] = normalized_content
            if "<" not in normalized_content and ">" not in normalized_content:
                attributes["content_json_string"] = json.dumps(
                    _plain_text_document(normalized_content)
                )
        if teaser_text is not None:
            attributes["teaser_text"] = teaser_text.replace("\r\n", "\n").replace("\r", "\n")
        if post_type is not None:
            if post_type not in POST_TYPES:
                raise PatreonError(
                    f"Unsupported post_type. Supported: {', '.join(sorted(POST_TYPES))}."
                )
            attributes["post_type"] = post_type
        if visibility is not None:
            if visibility not in POST_VISIBILITIES:
                raise PatreonError("visibility must be 'public', 'paid', or 'tiers'.")
            attributes["post_visibility"] = "paid" if visibility == "tiers" else visibility
        if scheduled_for is not None:
            attributes["scheduled_for"] = _normalize_scheduled_for(scheduled_for)
        if publish is not None:
            attributes["tags"] = {"publish": bool(publish)}

        relationships: dict[str, Any] = {}
        included: list[dict[str, Any]] = []

        if visibility == "tiers":
            tier_ids = tier_ids or []
            campaign = await self._campaign_id(campaign_id)
            rule_ids = await self._access_rule_ids(campaign, tier_ids)
            relationships["access_rules"] = {
                "data": [{"type": "access-rule", "id": rule_id} for rule_id in rule_ids]
            }
            included.extend({"type": "access-rule", "id": rule_id} for rule_id in rule_ids)
        elif visibility in {"public", "paid"}:
            relationships["access_rules"] = {"data": []}
        elif tier_ids:
            raise PatreonError("tier_ids may only be used with visibility='tiers'.")

        if tags is not None:
            normalized_tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
            relationships["user_defined_tags"] = {
                "data": [
                    {"type": "post_tag", "id": f"user_defined;{tag}"} for tag in normalized_tags
                ]
            }
            included.extend(
                {
                    "type": "post_tag",
                    "id": f"user_defined;{tag}",
                    "attributes": {"value": tag, "cardinality": 1},
                }
                for tag in normalized_tags
            )

        data: dict[str, Any] = {
            "type": "post",
            "attributes": attributes,
            "meta": {"auto_save": False},
        }
        if relationships:
            data["relationships"] = relationships
        body: dict[str, Any] = {"data": data}
        if included:
            body["included"] = included
        return body

    async def create_post(
        self,
        *,
        title: str,
        content: str | None = None,
        teaser_text: str | None = None,
        visibility: str = "paid",
        tier_ids: Sequence[str] | None = None,
        publish: bool = False,
        scheduled_for: str | None = None,
        tags: Sequence[str] | None = None,
        campaign_id: str | None = None,
    ) -> Post:
        if visibility not in POST_VISIBILITIES:
            raise PatreonError("visibility must be 'public', 'paid', or 'tiers'.")
        title = title.strip()
        if not title:
            raise PatreonError("title must not be empty.")
        if scheduled_for and not publish:
            raise PatreonError("scheduled_for requires publish=true.")
        if visibility == "tiers" and not tier_ids:
            raise PatreonError("tier_ids is required when visibility is 'tiers'.")
        if visibility != "tiers" and tier_ids:
            raise PatreonError("tier_ids may only be used with visibility='tiers'.")

        create_body = {
            "data": {
                "type": "post",
                "attributes": {
                    "post_type": "text_only",
                    "post_visibility": "paid" if visibility == "tiers" else visibility,
                },
            }
        }
        created = await self.client.request("POST", "/api/posts/new", json_body=create_body)
        data = created.get("data")
        if not isinstance(data, dict) or data.get("id") is None:
            raise PatreonError(
                "Patreon created no post record. The web session may lack creator permissions."
            )
        post_id = str(data["id"])

        try:
            return await self.update_post(
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
                post_type="text_only",
            )
        except PatreonError as exc:
            raise PatreonError(
                f"Created draft post {post_id}, but saving it failed: {exc}"
            ) from exc

    async def update_post(
        self,
        post_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        teaser_text: str | None = None,
        visibility: str | None = None,
        tier_ids: Sequence[str] | None = None,
        publish: bool | None = None,
        scheduled_for: str | None = None,
        tags: Sequence[str] | None = None,
        campaign_id: str | None = None,
        post_type: str | None = None,
    ) -> Post:
        post_id = _validate_id(post_id, "post_id")
        if (
            title is None
            and content is None
            and teaser_text is None
            and visibility is None
            and tier_ids is None
            and publish is None
            and scheduled_for is None
            and tags is None
        ):
            raise PatreonError("Provide at least one field to update.")

        if scheduled_for is not None and not publish:
            raise PatreonError("scheduled_for requires publish=true.")

        payload = await self._build_post_payload(
            title=title,
            content=content,
            teaser_text=teaser_text,
            visibility=visibility,
            tier_ids=tier_ids,
            publish=publish,
            scheduled_for=scheduled_for,
            tags=tags,
            campaign_id=campaign_id,
            post_type=post_type,
        )
        response = await self.client.request(
            "PATCH",
            f"/api/posts/{post_id}",
            json_body=payload,
            csrf_uri=f"/posts/{post_id}/edit",
        )
        data = response.get("data")
        if isinstance(data, dict) and data.get("attributes"):
            return _normalize_post(data, _included_index(response))
        return await self.get_post(post_id)

    async def get_post(self, post_id: str) -> Post:
        post_id = _validate_id(post_id, "post_id")
        response = await self.client.request(
            "GET",
            f"/api/posts/{post_id}",
            params=_jsonapi_params(
                {
                    "fields[post]": _csv(self.POST_FIELDS),
                    "include": "access_rules",
                    "fields[access-rule]": "access_rule_type",
                }
            ),
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise PatreonError("Patreon returned no post data.")
        return _normalize_post(data, _included_index(response))

    async def delete_post(self, post_id: str) -> DeleteResult:
        post_id = _validate_id(post_id, "post_id")
        await self.client.request(
            "DELETE",
            f"/api/posts/{post_id}",
            csrf_uri=f"/posts/{post_id}/edit",
        )
        return DeleteResult(id=post_id)

    async def list_drafts(
        self,
        campaign_id: str | None = None,
        *,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> PostPage:
        _validate_page_size(page_size)
        resolved_campaign = await self._campaign_id(campaign_id)
        params = _jsonapi_params(
            {
                "fields[post]": _csv(self.POST_FIELDS),
                "include": "access_rules",
                "fields[access-rule]": "access_rule_type",
                "filter[campaign_id]": resolved_campaign,
                "filter[is_draft]": "true",
                "page[count]": str(page_size),
            }
        )
        if cursor:
            params["page[cursor]"] = cursor
        response = await self.client.request("GET", "/api/posts", params=params)
        index = _included_index(response)
        return PostPage(
            posts=[_normalize_post(item, index) for item in _resource_list(response)],
            next_cursor=_next_cursor(response),
            total=_total(response),
        )
