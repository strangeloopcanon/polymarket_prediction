from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HttpConfig:
    user_agent: str = "polymarket-watch/0.1.0"
    timeout_s: float = 10.0
    min_interval_s: float = 0.10
    max_retries: int = 3


class RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = max(0.0, min_interval_s)
        self._last_request_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        sleep_s = self._min_interval_s - (now - self._last_request_at)
        if sleep_s > 0:
            time.sleep(sleep_s)
        self._last_request_at = time.monotonic()


class HttpClient:
    def __init__(self, config: HttpConfig | None = None) -> None:
        self._config = config or HttpConfig()
        self._limiter = RateLimiter(self._config.min_interval_s)

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() != "https":
            raise ValueError("only https:// URLs are allowed")
        if not parsed.netloc:
            raise ValueError("URL must include a hostname")
        return url

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            url = f"{url}?{query}"
        url = self._validate_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": self._config.user_agent})

        last_exc: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            self._limiter.wait()
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_s) as resp:  # nosec B310
                    body = resp.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                last_exc = e
                status = getattr(e, "code", None)
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if status in {429, 500, 502, 503, 504} and attempt < self._config.max_retries:
                    backoff_s = 0.5 * (2**attempt)
                    if retry_after:
                        try:
                            backoff_s = max(backoff_s, float(retry_after))
                        except ValueError:
                            pass
                    time.sleep(backoff_s)
                    continue
                raise
            except urllib.error.URLError as e:
                last_exc = e
                if attempt < self._config.max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise
        raise RuntimeError("unreachable") from last_exc

    def post_json(self, url: str, payload: dict[str, Any]) -> None:
        url = self._validate_url(url)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": self._config.user_agent,
            },
            method="POST",
        )
        self._limiter.wait()
        with urllib.request.urlopen(req, timeout=self._config.timeout_s):  # nosec B310
            return
