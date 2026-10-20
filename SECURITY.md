# Security

## Reporting

Please report vulnerabilities privately through GitHub's security advisories for
this repository. Do not open a public issue for a vulnerability.

## Credentials

This project handles two sensitive credentials:

- `PATREON_ACCESS_TOKEN` / OAuth refresh tokens
- `PATREON_SESSION_COOKIE`, a full logged-in Patreon browser session

Never commit either value, include it in an issue, or paste it into a chat.
Prefer `PATREON_TOKEN_FILE` and `PATREON_SESSION_COOKIE_FILE` with mode `0600`.

## Undocumented web API

Post write tools use Patreon's internal web API, not the public OAuth API. Those
routes can change without notice and are outside Patreon's public API contract.
Use them only with your own account and review the payloads before publishing.
