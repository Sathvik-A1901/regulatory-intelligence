"""Shared HTTP plumbing: polite rate limiting, retries, and a content-addressed disk cache.

The cache is what makes ingestion incremental. A section version is fetched once,
ever; a re-run only touches dates the cache has not seen.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import httpx

DEFAULT_CONTACT = os.environ.get("REGINTEL_CONTACT", "regintel-research (set REGINTEL_CONTACT)")
USER_AGENT = f"regintel/0.1 (+{DEFAULT_CONTACT})"


class PoliteClient:
    def __init__(self, cache_dir: Path, min_interval: float = 0.25, timeout: float = 60.0):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._last = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            follow_redirects=True,
        )
        self.hits = 0
        self.misses = 0

    def _cache_path(self, url: str, params: dict | None, suffix: str) -> Path:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.cache_dir / digest[:2] / f"{digest}{suffix}"

    def get_text(self, url: str, params: dict | None = None, suffix: str = ".txt",
                 use_cache: bool = True) -> str | None:
        """Return the body, or None on 404. Retries 429/5xx with backoff."""
        path = self._cache_path(url, params, suffix)
        if use_cache and path.exists():
            self.hits += 1
            return path.read_text(encoding="utf-8")

        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError:
                time.sleep(2**attempt)
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            self.misses += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(resp.text, encoding="utf-8")
            return resp.text
        raise RuntimeError(f"Gave up on {url} {params} after retries")

    def close(self) -> None:
        self._client.close()
