# Contributing

Issues and pull requests are welcome.

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests use `httpx2.MockTransport`; they never hit Patreon and need no credentials.

## Guidelines

- Keep the official API client (`api.py`, `client.py`) separate from the
  undocumented web client (`web_api.py`).
- Do not commit tokens, cookies, or `.env` files.
- Add tests for new request payloads, normalization, pagination, and error
  mapping.
- Treat Patreon's `/api` web routes as unstable. Document any endpoint or
  payload assumption you add.
- Do not add browser automation to this project. The web client talks directly
  to Patreon's JSON:API routes.
