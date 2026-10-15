"""Small HTTP helper for GitHub and raw file hosts."""

from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request

USER_AGENT = "skillrepo/0.1"
MAX_ATTEMPTS = 3


class RateLimitError(RuntimeError):
    def __init__(self, resource: str, reset_at: float):
        super().__init__(f"GitHub {resource} rate limit exceeded")
        self.resource = resource
        self.reset_at = reset_at


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:180]}")
        self.status = status
        self.url = url


def request(
    url: str,
    *,
    timeout: float = 40,
    max_bytes: int | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """GET a URL and return status, headers, and a possibly truncated body."""

    merged = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if headers:
        merged.update(headers)
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                header_map = {key: value for key, value in response.headers.items()}
                if max_bytes is None:
                    body = response.read()
                else:
                    body = response.read(max_bytes + 1)[:max_bytes]
                _raise_if_rate_limited(status, header_map, body)
                return status, header_map, body
        except RateLimitError:
            raise
        except urllib.error.HTTPError as error:
            detail = error.read(400).decode("utf-8", errors="replace")
            header_map = {key: value for key, value in error.headers.items()} if error.headers else {}
            if error.code in {502, 503, 504} and attempt < MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
                last_error = error
                continue
            try:
                _raise_if_rate_limited(error.code, header_map, detail.encode())
            except RateLimitError:
                raise
            raise HttpStatusError(error.code, url, detail) from error
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS:
                time.sleep(1.5 * attempt)
                continue
            raise
    raise RuntimeError(f"request failed for {url}") from last_error


def _raise_if_rate_limited(status: int, headers: dict[str, str], body: bytes) -> None:
    if status != 403:
        return
    text = body.decode("utf-8", errors="replace").lower()
    if "rate limit" not in text and "secondary rate" not in text:
        return
    resource = headers.get("X-RateLimit-Resource", "core")
    try:
        reset_at = float(headers.get("X-RateLimit-Reset", "0"))
    except ValueError:
        reset_at = 0
    raise RateLimitError(resource, reset_at)


def header_int(headers: dict[str, str], name: str) -> int | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            try:
                return int(value)
            except ValueError:
                return None
    return None
