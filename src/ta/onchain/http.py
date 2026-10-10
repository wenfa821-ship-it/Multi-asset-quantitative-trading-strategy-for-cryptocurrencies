"""Tiny HTTP client with retries, on-disk cache and a fully offline mode.

The fetcher is injectable, so every test in this repo runs with zero network.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

JsonFetcher = Callable[[str, str, dict[str, str], dict[str, Any] | None, float], dict[str, Any]]


class HttpError(RuntimeError):
    pass


def urllib_fetcher(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    send_headers = {"accept": "application/json", **headers}
    if data is not None:
        send_headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=send_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise HttpError(f"{method} {url} -> HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"{method} {url} -> {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpError(f"{method} {url} -> timeout after {timeout}s") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HttpError(f"{method} {url} -> non-JSON response") from exc


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 8.0,
        retries: int = 2,
        backoff: float = 0.5,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        fetcher: JsonFetcher | None = None,
        user_agent: str = "trading-agent/0.1 (+personal research)",
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.fetcher = fetcher or urllib_fetcher
        self.headers = {"user-agent": user_agent}
        self.hits = 0
        self.misses = 0
        self.cache_hits = 0
        # where responses actually came from: "network" | "cache" | "offline"
        self.source_counts: Counter[str] = Counter()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- public ------------------------------------------------------------
    def get_json(
        self, url: str, headers: dict[str, str] | None = None, cache_key: str | None = None
    ) -> dict[str, Any]:
        return self._call("GET", url, None, headers, cache_key)

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        return self._call("POST", url, payload, headers, cache_key)

    # ---- internals ---------------------------------------------------------
    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        if not path or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, key: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(key)
        if not path:
            return
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _call(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None,
        cache_key: str | None,
    ) -> dict[str, Any]:
        key = cache_key or f"{method} {url} {json.dumps(body, sort_keys=True) if body else ''}"

        # Offline mode means "use the injected source" (fixtures in tests/rehearsals).
        # It deliberately never reads or writes the cache: recorded fixtures must not
        # leak into a later live run through the cache.
        if self.offline:
            try:
                payload = self.fetcher(method, url, {**self.headers, **(headers or {})}, body, self.timeout)
            except Exception as exc:  # noqa: BLE001
                raise HttpError(
                    f"offline mode: {method} {url} unavailable ({exc}). Point "
                    "onchain.data.fixture_dir at a directory with recorded payloads, or run "
                    "with data.offline=false."
                ) from exc
            self.source_counts["offline"] += 1
            return payload

        merged = {**self.headers, **(headers or {})}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                payload = self.fetcher(method, url, merged, body, self.timeout)
                self.misses += 1
                self.source_counts["network"] += 1
                self._write_cache(key, payload)
                return payload
            except Exception as exc:  # noqa: BLE001 - retried below, then re-raised
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * (2**attempt))

        # network exhausted: a cached response is better than nothing, but it must be
        # visible in source_counts so nobody mistakes stale data for a fresh quote
        cached = self._read_cache(key)
        if cached is not None:
            self.cache_hits += 1
            self.source_counts["cache"] += 1
            return cached
        raise HttpError(f"{method} {url} failed after {self.retries + 1} attempts: {last_error}")
