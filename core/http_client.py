from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MIN_INTERVAL_SECONDS = 0.25


class FetchError(RuntimeError):
    def __init__(self, url: str, message: str, status_code: int | None = None, attempt: int | None = None):
        self.url = url
        self.status_code = status_code
        self.attempt = attempt
        details = [f"url={url}"]
        if status_code is not None:
            details.append(f"status={status_code}")
        if attempt is not None:
            details.append(f"attempt={attempt}")
        super().__init__(f"{message} ({', '.join(details)})")


_last_request_at = 0.0


def fetch_text(url: str, user_agent: str, timeout: int | None = None) -> str:
    body, charset = _send(
        "GET",
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=timeout,
    )
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode(charset or "utf-8", errors="replace")


def fetch_json(url: str, user_agent: str, timeout: int | None = None) -> object:
    text = fetch_text(url, user_agent, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(url, f"Invalid JSON response: {exc.msg}") from exc


def fetch_json_post(
    url: str,
    body: str,
    user_agent: str,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> Any:
    request_headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if headers:
        request_headers.update(headers)

    response_body, charset = _send(
        "POST",
        url,
        headers=request_headers,
        content=body.encode("utf-8"),
        timeout=timeout,
    )
    try:
        return json.loads(response_body.decode(charset or "utf-8"))
    except json.JSONDecodeError as exc:
        raise FetchError(url, f"Invalid JSON response: {exc.msg}") from exc


def _send(
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes | None = None,
    timeout: int | None = None,
) -> tuple[bytes, str | None]:
    timeout_seconds = timeout if timeout is not None else _int_env("UPCOMING_GAMES_HTTP_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
    retries = max(0, _int_env("UPCOMING_GAMES_HTTP_RETRIES", DEFAULT_RETRIES))
    backoff_seconds = max(0.0, _float_env("UPCOMING_GAMES_HTTP_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS))

    for attempt_index in range(retries + 1):
        attempt = attempt_index + 1
        try:
            _throttle()
            with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
                response = client.request(method, url, headers=headers, content=content)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt_index < retries:
                _sleep_before_retry(response.headers, backoff_seconds, attempt_index)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise FetchError(
                    url,
                    "HTTP request failed",
                    status_code=response.status_code,
                    attempt=attempt,
                ) from exc
            return response.content, response.encoding
        except httpx.TransportError as exc:
            if attempt_index >= retries:
                raise FetchError(url, f"Network request failed: {exc}", attempt=attempt) from exc
            _sleep_before_retry(None, backoff_seconds, attempt_index)

    raise FetchError(url, "HTTP request failed after retries", attempt=retries + 1)


def _throttle() -> None:
    global _last_request_at
    interval = max(0.0, _float_env("UPCOMING_GAMES_HTTP_MIN_INTERVAL_SECONDS", DEFAULT_MIN_INTERVAL_SECONDS))
    if interval <= 0:
        return

    now = time.monotonic()
    wait_seconds = interval - (now - _last_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
        now = time.monotonic()
    _last_request_at = now


def _sleep_before_retry(headers: Any, backoff_seconds: float, attempt_index: int) -> None:
    retry_after = _retry_after_seconds(headers)
    delay = retry_after if retry_after is not None else backoff_seconds * (2 ** attempt_index)
    if delay > 0:
        time.sleep(delay)


def _retry_after_seconds(headers: Any) -> float | None:
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default
