"""HTTP and OAuth token handling for the Patreon API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

import httpx2

from . import __version__
from .config import ConfigError, Settings, _read_token_file

logger = logging.getLogger(__name__)

USER_AGENT = f"patreonmcp/{__version__}"
_REFRESH_MARGIN_SECONDS = 60.0
_MAX_TRANSIENT_RETRIES = 1
_MAX_RETRY_WAIT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 8_388_608
_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"})


class PatreonError(Exception):
    """A sanitized, user-facing Patreon failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")
        if self.code:
            parts.append(f"[{self.code}]")
        if self.retry_after is not None:
            parts.append(f"Retry after {self.retry_after:g}s.")
        return " ".join(parts)


class PatreonAuthError(PatreonError):
    """Authentication or token-refresh failure."""


def _error_details(payload: Any) -> tuple[str | None, str | None, float | None]:
    """Extract detail, code, and retry delay from a Patreon error payload."""

    if not isinstance(payload, dict):
        return None, None, None
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        description = payload.get("error_description") or payload.get("error")
        return (str(description) if description else None), None, None

    first = errors[0] if isinstance(errors[0], dict) else {}
    detail = first.get("detail") or first.get("title")
    code = first.get("code_name") or first.get("code")
    retry_after = first.get("retry_after_seconds")
    try:
        retry = float(retry_after) if retry_after is not None else None
    except (TypeError, ValueError):
        retry = None
    return (
        str(detail) if detail else None,
        str(code) if code else None,
        retry,
    )


def _header_retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class PatreonClient:
    """Small async client with one-shot refresh and conservative retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._refresh_lock = asyncio.Lock()
        self._token_generation = 0
        self._http = httpx2.AsyncClient(
            base_url=settings.api_base_url.rstrip("/") + "/",
            timeout=settings.timeout_seconds,
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    async def __aenter__(self) -> PatreonClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def access_token(self) -> str | None:
        return self.settings.access_token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an authenticated API request and return decoded JSON."""

        await self._ensure_token()
        transient_retries = 0
        auth_retried = False

        while True:
            generation = self._token_generation
            try:
                response = await self._http.request(
                    method,
                    path.lstrip("/"),
                    params=params,
                    json=json_body,
                    headers=self._auth_header(),
                )
            except httpx2.HTTPError as exc:
                raise PatreonError(
                    f"Could not reach Patreon ({type(exc).__name__}): {exc}"
                ) from exc

            if response.status_code == 401 and not auth_retried:
                if not self.settings.can_refresh:
                    raise PatreonAuthError(
                        "Patreon rejected the access token. Set a valid "
                        "PATREON_ACCESS_TOKEN, or configure PATREON_REFRESH_TOKEN, "
                        "PATREON_CLIENT_ID, and PATREON_CLIENT_SECRET.",
                        status_code=401,
                        code="unauthorized",
                    )
                await self._refresh(failed_generation=generation)
                auth_retried = True
                continue

            if response.status_code == 429:
                retry_after = _header_retry_after(response)
                if retry_after is None:
                    _, _, retry_after = _error_details(self._maybe_json(response))
                if (
                    transient_retries < _MAX_TRANSIENT_RETRIES
                    and retry_after is not None
                    and retry_after <= _MAX_RETRY_WAIT_SECONDS
                ):
                    transient_retries += 1
                    await self._sleep(retry_after)
                    continue
                self._raise_for_response(response)

            if response.status_code in (502, 503, 504):
                if (
                    transient_retries < _MAX_TRANSIENT_RETRIES
                    and method.upper() in _RETRYABLE_METHODS
                ):
                    transient_retries += 1
                    await self._sleep(0.5 * transient_retries)
                    continue
                self._raise_for_response(response)

            if response.status_code >= 400:
                self._raise_for_response(response)

            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise PatreonError(
                            "Patreon response exceeded the 8 MiB response limit.",
                            status_code=response.status_code,
                        )
                except ValueError:
                    pass

            if response.status_code == 204 or not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError as exc:
                raise PatreonError(
                    "Patreon returned a response that was not valid JSON.",
                    status_code=response.status_code,
                ) from exc
            if not isinstance(payload, dict):
                raise PatreonError(
                    "Patreon returned an unexpected response shape.",
                    status_code=response.status_code,
                )
            return payload

    def _auth_header(self) -> dict[str, str]:
        if not self.settings.access_token:
            raise PatreonAuthError(
                "No Patreon access token is available. Configure OAuth refresh "
                "credentials or set PATREON_ACCESS_TOKEN."
            )
        return {"Authorization": f"Bearer {self.settings.access_token}"}

    async def _ensure_token(self) -> None:
        if not self.settings.access_token:
            if not self.settings.can_refresh:
                raise PatreonAuthError(
                    "No Patreon access token is available. Configure OAuth refresh "
                    "credentials or set PATREON_ACCESS_TOKEN."
                )
            await self._refresh(failed_generation=self._token_generation)
            return

        expires_at = self.settings.expires_at
        if (
            expires_at is not None
            and expires_at <= self._clock() + _REFRESH_MARGIN_SECONDS
            and self.settings.can_refresh
        ):
            await self._refresh(failed_generation=self._token_generation)

    @contextlib.asynccontextmanager
    async def _token_file_lock(self) -> AsyncIterator[None]:
        path = self.settings.token_file
        if path is None:
            yield
            return
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            yield
            return

        lock_path = path.with_name(path.name + ".lock")
        fd = -1
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = await asyncio.to_thread(os.open, lock_path, flags, 0o600)
            await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX)
        except OSError as exc:
            logger.warning("Could not lock Patreon token file %s: %s", path, exc)
            if fd >= 0:
                os.close(fd)
            yield
            return

        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _adopt_token_file(self) -> bool:
        path = self.settings.token_file
        if path is None:
            return False
        try:
            stored = _read_token_file(path)
        except ConfigError as exc:
            logger.warning("Could not re-read Patreon token file %s: %s", path, exc)
            return False
        if not stored:
            return False

        stored_access = str(stored.get("access_token") or "").strip() or None
        stored_refresh = str(stored.get("refresh_token") or "").strip() or None
        stored_expires = stored.get("expires_at")
        try:
            expires_at = float(stored_expires) if stored_expires is not None else None
        except (TypeError, ValueError):
            expires_at = None

        if (
            stored_access == self.settings.access_token
            and expires_at is not None
            and expires_at > self._clock() + _REFRESH_MARGIN_SECONDS
        ):
            self.settings = replace(self.settings, expires_at=expires_at)
            self._token_generation += 1
            return True
        if (
            stored_access
            and stored_access != self.settings.access_token
            and (expires_at is None or expires_at > self._clock() + _REFRESH_MARGIN_SECONDS)
        ):
            self.settings = replace(
                self.settings,
                access_token=stored_access,
                refresh_token=stored_refresh or self.settings.refresh_token,
                expires_at=expires_at,
            )
            self._token_generation += 1
            return True
        if stored_refresh and stored_refresh != self.settings.refresh_token:
            self.settings = replace(self.settings, refresh_token=stored_refresh)
        return False

    async def _refresh(self, *, failed_generation: int) -> None:
        async with self._refresh_lock:
            if self._token_generation != failed_generation:
                return
            async with self._token_file_lock():
                if self._adopt_token_file():
                    return
                await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        if not self.settings.can_refresh:
            raise PatreonAuthError(
                "The access token expired, but OAuth refresh credentials are "
                "incomplete. Set PATREON_REFRESH_TOKEN, PATREON_CLIENT_ID, and "
                "PATREON_CLIENT_SECRET.",
                status_code=401,
                code="token_refresh_unavailable",
            )

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.settings.refresh_token,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
        }
        try:
            response = await self._http.post(
                self.settings.token_url,
                data=data,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        except httpx2.HTTPError as exc:
            raise PatreonAuthError(
                f"Could not reach the Patreon token endpoint ({type(exc).__name__}): {exc}"
            ) from exc

        payload = self._maybe_json(response)
        if response.status_code >= 400:
            detail, code, _ = _error_details(payload)
            raise PatreonAuthError(
                detail or "Patreon rejected the OAuth refresh request.",
                status_code=response.status_code,
                code=code or "token_refresh_failed",
            )

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise PatreonAuthError(
                "Patreon's token endpoint returned no access token.",
                status_code=response.status_code,
                code="token_refresh_failed",
            )

        expires_in = payload.get("expires_in")
        try:
            expires_at = self._clock() + float(expires_in)
        except (TypeError, ValueError):
            expires_at = None

        self.settings = self.settings.with_tokens(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_at=expires_at,
        )
        self._token_generation += 1
        self._save_tokens(payload, expires_at)

    def _save_tokens(self, payload: Mapping[str, Any], expires_at: float | None) -> None:
        path = self.settings.token_file
        if path is None:
            return
        document = {
            "access_token": self.settings.access_token,
            "refresh_token": self.settings.refresh_token,
            "expires_at": expires_at,
            "token_type": payload.get("token_type", "Bearer"),
            "scope": payload.get("scope"),
        }
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(temp_path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
                handle.write("\n")
            os.replace(temp_path, path)
            path.chmod(0o600)
        except OSError as exc:
            with contextlib.suppress(OSError):
                temp_path.unlink(missing_ok=True)
            logger.warning("Could not persist refreshed Patreon tokens to %s: %s", path, exc)

    @staticmethod
    def _maybe_json(response: httpx2.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _raise_for_response(self, response: httpx2.Response) -> None:
        payload = self._maybe_json(response)
        detail, code, retry_after = _error_details(payload)
        if retry_after is None:
            retry_after = _header_retry_after(response)

        if response.status_code == 401:
            message = detail or (
                "Patreon rejected the access token. Set a valid PATREON_ACCESS_TOKEN "
                "or configure OAuth refresh credentials."
            )
            error_type: type[PatreonError] = PatreonAuthError
        elif response.status_code == 403:
            message = detail or (
                "Patreon denied this request. The token is missing a required OAuth "
                "scope or campaign permission."
            )
            error_type = PatreonError
        elif response.status_code == 404:
            message = detail or "The requested Patreon resource was not found."
            error_type = PatreonError
        elif response.status_code == 429:
            message = detail or "Patreon rate limit reached."
            error_type = PatreonError
        else:
            message = detail or f"Patreon returned HTTP {response.status_code}."
            error_type = PatreonError

        raise error_type(
            message,
            status_code=response.status_code,
            code=code,
            retry_after=retry_after,
        )
