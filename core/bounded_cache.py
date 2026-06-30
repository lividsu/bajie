from __future__ import annotations

import sqlite3
import time
from pathlib import Path
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


class PersistentTTLBoundedSet:
    """A small thread-safe set with TTL/max-size eviction backed by SQLite."""

    def __init__(self, db_path: str | Path, max_size: int = 10000, ttl_seconds: int = 86400):
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.db_path = Path(db_path)
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._lock = RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                item TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at)"
        )

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        now = time.time()
        with self._lock:
            self._prune(now)
            cursor = self._conn.execute("SELECT 1 FROM items WHERE item = ?", (item,))
            return cursor.fetchone() is not None

    def try_add(self, item: str) -> bool:
        """Atomically check and add. Returns True if added, False if already present."""
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._prune(now)
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO items (item, created_at) VALUES (?, ?)",
                    (item, now),
                )
                added = cursor.rowcount == 1
                self._prune_to_max_size()
                self._conn.execute("COMMIT")
                return added
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def add(self, item: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._prune(now)
                self._conn.execute(
                    """
                    INSERT INTO items (item, created_at) VALUES (?, ?)
                    ON CONFLICT(item) DO UPDATE SET created_at = excluded.created_at
                    """,
                    (item, now),
                )
                self._prune_to_max_size()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def __len__(self) -> int:
        with self._lock:
            self._prune(time.time())
            cursor = self._conn.execute("SELECT COUNT(*) FROM items")
            return int(cursor.fetchone()[0])

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            self._prune(time.time())
            cursor = self._conn.execute("SELECT item FROM items ORDER BY created_at, item")
            return iter(tuple(row[0] for row in cursor.fetchall()))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        self._conn.execute("DELETE FROM items WHERE created_at < ?", (cutoff,))

    def _prune_to_max_size(self) -> None:
        self._conn.execute(
            """
            DELETE FROM items
            WHERE item IN (
                SELECT item
                FROM items
                ORDER BY created_at ASC, item ASC
                LIMIT MAX(
                    (SELECT COUNT(*) FROM items) - ?,
                    0
                )
            )
            """,
            (self.max_size,),
        )
