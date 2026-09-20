"""Bounded, single-attempt HTTPS transport; no credentials or I/O at import."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .the_odds_api_live import MLBOddsEventRequest


@dataclass(frozen=True, slots=True)
class OddsAPIHTTPResponse:
    status_code: int | None
    headers: tuple[tuple[str, str], ...]
    body: bytes
    error: str | None = None


class OddsAPITransport(Protocol):
    def send(self, request: MLBOddsEventRequest, *, api_key: str,
             timeout_seconds: float, max_response_bytes: int) -> OddsAPIHTTPResponse: ...


class RequestsOddsAPITransport:
    """Explicitly enabled HTTPS only. No redirects, retries or environment auth.

    Streamed decoded bytes and Content-Length are bounded. The caller's default
    is 4 MiB. Only accounting headers and Retry-After reach the executor, which
    strictly sanitizes their values before creating any evidence.
    """

    def __init__(self, *, network_enabled: bool = False) -> None:
        self.network_enabled = network_enabled

    def send(self, request: MLBOddsEventRequest, *, api_key: str,
             timeout_seconds: float, max_response_bytes: int) -> OddsAPIHTTPResponse:
        if self.network_enabled is not True:
            return OddsAPIHTTPResponse(None, (), b"", "NETWORK_DISABLED")
        from .the_odds_api_live import _validate_request, _positive_timeout, _integer
        try:
            _validate_request(request)
            _positive_timeout(timeout_seconds)
            _integer(max_response_bytes, minimum=1, maximum=16 * 1024 * 1024)
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError("MISSING_API_KEY")
        except (ValueError, TypeError, AttributeError):
            return OddsAPIHTTPResponse(None, (), b"", "INVALID_REQUEST")
        import requests
        import http.client
        import logging
        import urllib3.connection

        # urllib3 DEBUG and http.client wire debugging can print query keys.
        # Refuse before preparing an authenticated request; change no global
        # logging policy. Callers must keep runtime logging configuration stable.
        if any(logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in (
            "urllib3.connectionpool", "urllib3.util.retry",
        )) or any(connection.debuglevel for connection in (
            http.client.HTTPConnection, http.client.HTTPSConnection,
            urllib3.connection.HTTPConnection, urllib3.connection.HTTPSConnection,
        )):
            return OddsAPIHTTPResponse(None, (), b"", "UNSAFE_HTTP_LOGGING")

        status = None
        safe_headers = ()
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
                params = dict(request.query)
                params["apiKey"] = api_key
                with session.get(
                    "https://api.the-odds-api.com" + request.endpoint,
                    params=params, timeout=timeout_seconds, stream=True,
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    safe_headers = tuple(
                        (str(k).lower(), str(v)) for k, v in response.headers.items()
                        if str(k).lower() in {
                            "x-requests-used", "x-requests-remaining", "x-requests-last",
                            "retry-after",
                        }
                    )
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isascii() or not length.isdigit()):
                        return OddsAPIHTTPResponse(status, safe_headers, b"", "INVALID_RESPONSE_SHAPE")
                    if length is not None and int(length) > max_response_bytes:
                        return OddsAPIHTTPResponse(status, safe_headers, b"", "RESPONSE_TOO_LARGE")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > max_response_bytes:
                            return OddsAPIHTTPResponse(status, safe_headers, b"", "RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
                    return OddsAPIHTTPResponse(status, safe_headers, b"".join(chunks))
        except requests.Timeout:
            return OddsAPIHTTPResponse(status, safe_headers, b"", "TIMEOUT")
        except Exception:
            # Client exception text can contain the full authenticated URL.
            return OddsAPIHTTPResponse(status, safe_headers, b"", "NETWORK_ERROR")
