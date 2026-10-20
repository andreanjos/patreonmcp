# Security

## Reporting

Please report vulnerabilities privately through GitHub's security advisories for
this repository. Do not open a public issue for a vulnerability.

## Credentials

This project handles three sensitive credentials:

- `PATREON_ACCESS_TOKEN` / OAuth refresh tokens
- `PATREON_SESSION_COOKIE`, a full logged-in Patreon browser session
- `PATREON_HTTP_BEARER_TOKEN`, the optional HTTP transport credential

Never commit any of these values, include them in an issue, or paste them into a chat.
Prefer `PATREON_TOKEN_FILE` and `PATREON_SESSION_COOKIE_FILE` with mode `0600`.
The server rejects symlinks and non-regular files; on POSIX it also rejects
wrong ownership and group/world-readable files. API, token, and web base URLs
are pinned to `www.patreon.com` (or loopback for local tests) so credentials
cannot be redirected by environment overrides.

## HTTP transport

The streamable-HTTP transport binds to loopback by default. Set
`PATREON_HTTP_BEARER_TOKEN` to require `Authorization: Bearer <token>` on every
request. Remote binding requires both `--allow-remote-http` and a bearer token.
`--allow-unauthenticated-http` is an additional explicit opt-in for deployments
behind an authenticated reverse proxy.

## Webhook writes

Webhook creation is disabled unless `PATREON_WEBHOOK_ALLOWED_HOSTS` is set.
Webhooks persist and can export future patron events, so treat URI changes and
unpausing as sensitive operations. Private, loopback, link-local, and reserved
IP targets are rejected.

## Data exposure

PII fields (patron email, creator notes, shipping addresses) are disabled
unless `PATREON_ALLOW_PII=1`. Webhook signing secrets and Live stream keys are
redacted by default. Post content is escaped unless `allow_html=true`, and post
content is capped at 100,000 characters.

## Undocumented web API

Post write tools use Patreon's internal web API, not the public OAuth API. Those
routes can change without notice and are outside Patreon's public API contract.
Use them only with your own account and review the payloads before publishing.
