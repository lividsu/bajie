from __future__ import annotations

import time
from threading import RLock
from typing import Iterator


class TTLBoundedSet:
    """A small thread-safe set with TTL and max-size eviction."""

    def __init__(self, max_size: int = 10000, ttl_seconds: int = 86400):
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, float] = {}
        self._lock = RLock()

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return item in self._items

    def try_add(self, item: str) -> bool:
        """Atomically check and add. Returns True if added (new), False if already present (duplicate)."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if item in self._items:
                return False
            self._items[item] = now
            while len(self._items) > self.max_size:
                oldest_key = next(iter(self._items))
                self._items.pop(oldest_key, None)
            return True

    def add(self, item: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._items[item] = now
            while len(self._items) > self.max_size:
                oldest_key = next(iter(self._items))
                self._items.pop(oldest_key, None)

    def __len__(self) -> int:
        with self._lock:
            self._prune(time.monotonic())
            return len(self._items)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            self._prune(time.monotonic())
            return iter(tuple(self._items))

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        expired = [key for key, created_at in self._items.items() if created_at < cutoff]
        for key in expired:
            self._items.pop(key, None)
