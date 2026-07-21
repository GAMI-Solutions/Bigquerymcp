"""TTL-based in-memory result cache, keyed on a hash of (sql, params).

Keeps repeated identical read queries from re-scanning (and re-billing) BigQuery.
Disabled entirely when cache_ttl_seconds == 0.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


def _cache_key(sql: str, params: Optional[list] = None, page_token: Optional[str] = None) -> str:
    param_repr = json.dumps(
        [(p.name, p.type_, p.value) for p in params] if params else [],
        default=str,
        sort_keys=True,
    )
    raw = f"{sql}||{param_repr}||{page_token or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class _Entry:
    value: Any
    expires_at: float


class QueryResultCache:
    """Thread-safe TTL cache. One instance is shared by the server process."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    def get(self, sql: str, params: Optional[list] = None, page_token: Optional[str] = None) -> Optional[Any]:
        if not self.enabled():
            return None
        key = _cache_key(sql, params, page_token)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at < time.time():
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    def set(self, sql: str, value: Any, params: Optional[list] = None, page_token: Optional[str] = None) -> None:
        if not self.enabled():
            return
        key = _cache_key(sql, params, page_token)
        with self._lock:
            self._store[key] = _Entry(value=value, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._store), "hits": self.hits, "misses": self.misses}
