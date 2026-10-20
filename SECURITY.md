# Security

## Reporting

Please report vulnerabilities privately through GitHub's security advisories for
this repository. Do not open a public issue for a vulnerability.

## Credentials

This project handles two sensitive credentials:

- `PATREON_ACCESS_TOKEN` / OAuth refresh tokens
- `PATREON_SESSION_COOKIE`, a full logged-in Patreon browser session

Never commit either value, include it in an issue, or paste it into a chat.
Prefer `PATREON_TOKEN_FILE` and `PATREON_SESSION_COOKIE_FILE` with mode `0600`;
the server rejects symlinks, non-regular files, wrong ownership, and
group/world-readable files. API, token, and web base URLs are pinned to
`www.patreon.com` (or loopback for local tests) so credentials cannot be
redirected by environment overrides.

## HTTP transport

The streamable-HTTP transport has no built-in authentication. It binds to
loopback by default. Do not expose it beyond loopback unless it is behind an
authenticated reverse proxy, and treat `--allow-remote-http` as a deliberate
risk decision.

## Webhook writes

Webhook creation is disabled unless `PATREON_WEBHOOK_ALLOWED_HOSTS` is set.
Webhooks persist and can export future patron events, so treat URI changes and
unpausing as sensitive operations. Private, loopback, link-local, and reserved
IP targets are rejected.

## Undocumented web API

Post write tools use Patreon's internal web API, not the public OAuth API. Those
routes can change without notice and are outside Patreon's public API contract.
Use them only with your own account and review the payloads before publishing.
