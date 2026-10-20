# patreonmcp

[![CI](https://github.com/andreanjos/patreonmcp/actions/workflows/ci.yml/badge.svg)](https://github.com/andreanjos/patreonmcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

MCP server for the official [Patreon API v2](https://docs.patreon.com/). Typed
tools for campaigns, tiers, members, posts, webhooks, and early-access Lives,
plus post creation and editing through Patreon's web editor API.

Python 3.10+.

## Features

- Typed MCP tools with structured JSON output.
- Identity, campaigns, tiers, members, and posts through the official API.
- Cursor pagination, explicit field selection, and opt-in PII fields.
- Automatic OAuth refresh with rotated-token persistence.
- Webhook CRUD (creation requires a host allowlist) and early-access Lives.
- Text post creation, editing, publishing, scheduling, and deletion through
  Patreon's web editor API when a browser session cookie is configured.
- No browser automation. The post client talks directly to Patreon's JSON:API
  routes.

## API limits worth knowing

The official Patreon API v2 has **no endpoints for creating, editing, or
deleting posts**:

- Read: identity, campaigns, tiers, members, posts.
- Write: webhooks and early-access Lives.

Patreon's own web app uses separate, undocumented JSON:API routes under
`/api/posts` with a browser session and CSRF ticket. This server speaks to those
routes directly for post management. They are not a public API, can change
without notice, and currently support text posts only. Post write tools require
`PATREON_SESSION_COOKIE` or `PATREON_SESSION_COOKIE_FILE`.

Member email, creator notes, and shipping addresses are opt-in tool parameters
because they are sensitive; email and address also require extra OAuth scopes.
They are also disabled unless the operator sets `PATREON_ALLOW_PII=1`.

## Install

```bash
git clone https://github.com/andreanjos/patreonmcp.git
cd patreonmcp
uv sync
```

Verify the server starts and your credentials work:

```bash
PATREON_ACCESS_TOKEN=your_token uv run patreonmcp check
```

For post write tools, `check` also verifies the browser session when
`PATREON_SESSION_COOKIE` is set.

Run it directly:

```bash
PATREON_ACCESS_TOKEN=your_token uv run patreonmcp serve
```

## Authentication

### Creator access token

Get your **Creator Access Token** from the
[Patreon Developer Portal](https://www.patreon.com/portal/registration/register-clients).
Creator tokens automatically carry all API v2 scopes for the creator's campaign.

```bash
export PATREON_ACCESS_TOKEN=...
```

### OAuth refresh (optional)

If your token expires, provide the OAuth client credentials and refresh token so
the server can renew it automatically:

```bash
export PATREON_CLIENT_ID=...
export PATREON_CLIENT_SECRET=...
export PATREON_REFRESH_TOKEN=...
```

Set `PATREON_TOKEN_FILE` to persist rotated tokens across restarts. The file is
created with mode `0600`; without it, refreshed tokens live only in memory.
Existing token and cookie files must already be `0600`, regular files, owned by
the current user, and not symlinks.

```bash
export PATREON_TOKEN_FILE="$HOME/.config/patreonmcp/tokens.json"
```

### Web session (post write tools)

The `create_post`, `update_post`, `publish_post`, `delete_post`,
`get_managed_post`, and `list_drafts` tools use Patreon's internal web API. They
need a logged-in browser session, not an OAuth token.

1. Sign in to <https://www.patreon.com> in a Chrome-family browser.
2. Open DevTools → Network, reload the page, and select a `www.patreon.com`
   request.
3. Copy its full `Cookie` request header. `document.cookie` is not enough
   because the session cookies are HttpOnly.
4. Put the copied header in `PATREON_SESSION_COOKIE`, or save it to a file and
   point `PATREON_SESSION_COOKIE_FILE` at it.

```bash
export PATREON_SESSION_COOKIE='session_id=...; a_csrf=...; cf_clearance=...'
# or
chmod 600 ~/.config/patreonmcp/cookies.txt
export PATREON_SESSION_COOKIE_FILE="$HOME/.config/patreonmcp/cookies.txt"
```

The cookie grants full access to the account. Keep it local, never commit it,
and log out of Patreon to invalidate it. The server fetches a CSRF ticket
automatically and never prints the cookie.

`patreonmcp check` verifies both auth modes when both are configured.

### Configuration reference

| Variable | Required | Purpose |
|---|---|---|
| `PATREON_ACCESS_TOKEN` | One auth mode | Bearer token for API calls. |
| `PATREON_REFRESH_TOKEN` | OAuth mode | Refresh token. |
| `PATREON_CLIENT_ID` | OAuth mode | OAuth client ID. |
| `PATREON_CLIENT_SECRET` | OAuth mode | OAuth client secret. |
| `PATREON_TOKEN_FILE` | No | Persist refreshed tokens as JSON (`0600`). |
| `PATREON_SESSION_COOKIE` | Web post tools | Raw `Cookie` header from a logged-in patreon.com session. |
| `PATREON_SESSION_COOKIE_FILE` | Web post tools | File containing the raw `Cookie` header. |
| `PATREON_API_BASE_URL` | No | API base; defaults to `https://www.patreon.com/api/oauth2/v2`. |
| `PATREON_TOKEN_URL` | No | Token endpoint; defaults to `https://www.patreon.com/api/oauth2/token`. |
| `PATREON_WEB_BASE_URL` | No | Web API base; defaults to `https://www.patreon.com`. |
| `PATREON_USER_AGENT` | No | Browser User-Agent for the web session; match the browser that issued the cookie if Cloudflare rejects it. |
| `PATREON_HTTP_BEARER_TOKEN` | No | Require `Authorization: Bearer <token>` on streamable-HTTP requests. Minimum 16 characters. |
| `PATREON_WEBHOOK_ALLOWED_HOSTS` | No | Required to enable webhook creation. Comma-separated hostname allowlist; `*` explicitly allows any public host. Private/loopback IP targets are rejected. |
| `PATREON_ALLOW_PII` | No | Set to `1` to enable patron email, creator notes, and shipping addresses. Disabled by default. |
| `PATREON_TIMEOUT_SECONDS` | No | HTTP timeout; defaults to `30`. |

The server reads process environment variables only. `.env.example` documents the
names; it is not loaded automatically. Use your shell, your MCP client's `env`
block, or `uv run --env-file .env ...`.

## MCP client configuration

Use an absolute path to this checkout. Example for Claude Desktop, Claude Code,
Cursor, or any client that accepts the standard `mcpServers` object:

```json
{
  "mcpServers": {
    "patreon": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/patreonmcp",
        "patreonmcp",
        "serve"
      ],
      "env": {
        "PATREON_ACCESS_TOKEN": "your_token_here",
        "PATREON_SESSION_COOKIE": "session_id=...; a_csrf=..."
      }
    }
  }
}
```

Omit `PATREON_SESSION_COOKIE` if you only need the read tools. Add
`PATREON_SESSION_COOKIE_FILE` instead when the cookie lives in a local file.

For HTTP transport instead of stdio:

```bash
PATREON_ACCESS_TOKEN=... uv run patreonmcp serve --transport streamable-http --host 127.0.0.1 --port 8000
```

The MCP endpoint is `/mcp`. Set `PATREON_HTTP_BEARER_TOKEN` to require a bearer
token on every HTTP request; clients then send
`Authorization: Bearer <token>`. Without it, the HTTP transport is
unauthenticated. It binds to loopback by default; binding elsewhere requires
`--allow-remote-http` and should only be done behind an authenticated reverse
proxy.

## Tools

| Tool | What it does | Scope |
|---|---|---|
| `get_identity` | Authenticated creator profile, optionally with campaign. | `identity` (+ `campaigns` for the campaign) |
| `list_campaigns` | Campaigns visible to the token, optionally with tiers. | `campaigns` |
| `get_campaign` | One campaign with tiers and creator. | `campaigns` |
| `list_members` | Paginated patrons; optional tiers, profile, email, note, address. | `campaigns.members` |
| `get_member` | One patron by member UUID. | `campaigns.members` |
| `list_posts` | Paginated posts; content is opt-in and capped at 100,000 characters per post. | `campaigns.posts` |
| `get_post` | One post; HTML content is capped at 100,000 characters. | `campaigns.posts` |
| `create_post` | Create a text post, draft or published; public, paid, or tier-gated. | Web session |
| `update_post` | Edit content/audience, publish, or schedule a post. | Web session |
| `publish_post` | Publish an existing draft. | Web session |
| `delete_post` | Delete a post permanently. | Web session |
| `get_managed_post` | Read a draft, scheduled, or published post. | Web session |
| `list_drafts` | Paginated unpublished drafts. | Web session |
| `list_webhooks` | Webhooks created by this API client; signing secrets redacted by default. | `w:campaigns.webhook` |
| `create_webhook` | Create a webhook; response includes the signing secret. | `w:campaigns.webhook` |
| `update_webhook` | Change URI, triggers, or paused state; unpause to redeliver. | `w:campaigns.webhook` |
| `delete_webhook` | Delete a webhook. | `w:campaigns.webhook` |
| `create_live` | Schedule or immediately start a Live; stream key redacted by default. | `w:campaigns.lives` |
| `get_live` | Live state and RTMP connection details; stream key redacted by default. | `campaigns.lives` |
| `update_live` | Start or end a Live; stream key redacted by default. | `w:campaigns.lives` |

### Pagination

`list_members` and `list_posts` return `next_cursor`. Pass it back as `cursor` to
fetch the next page. `page_size` accepts 1–1000; when post content is included,
the maximum is 100.

### Campaign resolution

Tools that need a campaign accept an optional `campaign_id`. If it is omitted and
the token can see exactly one campaign, the server uses it. If several are
visible, the tool fails with the visible IDs so the caller can choose.

### Post write behavior

`create_post` defaults to a draft (`publish=false`). Set `publish=true` to
publish immediately, or `publish=true` plus `scheduled_for` to schedule. Use
`visibility="tiers"` with `tier_ids` for tier-gated posts; `public` and `paid`
need no tier IDs. Only `text_only` posts are supported.

Post content and teaser text are escaped as plain text by default. Set
`allow_html=true` to publish raw HTML; that is not sanitized and can include
links, embeds, or tracking pixels.

`update_post` sends a partial update. `publish=true` publishes; omitting
`publish` saves without changing publication state. `delete_post` is permanent.

These tools are built on Patreon's browser API (`/api/posts/new` and
`PATCH /api/posts/{id}`) plus its CSRF ticket endpoint. They are not covered by
Patreon's public API contract and can break when Patreon changes its web app.

### Webhook triggers

`members:create`, `members:update`, `members:delete`,
`members:pledge:create`, `members:pledge:update`, `members:pledge:delete`,
`posts:publish`, `posts:update`, `posts:delete`.

Patreon signs webhook deliveries with `X-Patreon-Signature`, an HMAC-MD5 hex
digest of the request body using the webhook secret. Verify it on your receiver.

Webhook creation is disabled unless `PATREON_WEBHOOK_ALLOWED_HOSTS` names the
target host. Webhooks are a persistent data-export channel; approve URI changes
and unpausing carefully.

## Scopes

| Scope | Needed for |
|---|---|
| `identity` | `get_identity` |
| `identity[email]` | `get_identity(include_email=true)` |
| `campaigns` | campaigns and tiers |
| `campaigns.members` | members |
| `campaigns.members[email]` | `list_members` / `get_member` with email |
| `campaigns.members.address` | shipping addresses |
| `campaigns.posts` | posts |
| `w:campaigns.webhook` | webhook CRUD |
| `campaigns.lives` | reading Lives |
| `w:campaigns.lives` | creating and updating Lives |
| Web session cookie | post create/edit/publish/delete and draft listing |

Third-party OAuth clients must request these explicitly. During Patreon's OAuth
flow, newly requested scopes are added to previously approved scopes.

## Security defaults

- Patron email, creator notes, and shipping addresses require
  `PATREON_ALLOW_PII=1`.
- Webhook creation is disabled until `PATREON_WEBHOOK_ALLOWED_HOSTS` is set.
- `list_webhooks` redacts signing secrets and `get_live` redacts stream keys
  unless explicitly requested.
- Post content and teaser text are escaped unless `allow_html=true`; post
  content is capped at 100,000 characters.
- HTTP transport binds to loopback and refuses remote exposure without
  `--allow-remote-http`.
- Token and cookie files must be regular files owned by the current user with
  no group/other permissions (`chmod 600`).
- Base URLs are pinned to `www.patreon.com` (or loopback for tests), so a
  rogue environment cannot redirect bearer tokens, refresh tokens, or session
  cookies to another host.

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Layout:

```text
src/patreonmcp/
  api.py       # endpoint wrappers + JSON:API normalization
  client.py    # auth, token refresh, retries, rate-limit handling
  config.py    # environment, token-file, and session-cookie loading
  models.py    # typed structured-output models
  web_api.py   # undocumented /api post create/edit client + CSRF
  server.py    # MCP tool registration
  cli.py       # serve / check commands
```

Tests use `httpx2.MockTransport`; no network or real credentials are required.

## Privacy

Patron names, pledge amounts, charge status, and any optional email, note, or
address fields are sent to your MCP client and therefore to your model provider.
PII fields are disabled unless `PATREON_ALLOW_PII=1` is set.
Enable optional fields only when the task needs them, and handle the data in
accordance with Patreon's Creator Privacy Promise and applicable law.

`PATREON_SESSION_COOKIE` is a full account credential. Keep it in your local
environment or a `0600` file; never commit it, paste it into a chat, or share it
with the model provider.

## Contributing

Development setup, test commands, and review guidelines are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Do not report vulnerabilities in public issues. See [SECURITY.md](SECURITY.md).
Never commit a Patreon token or session cookie.

## License

MIT. See [LICENSE](LICENSE).
