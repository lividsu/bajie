import sqlite3
import time

from core.bounded_cache import PersistentTTLBoundedSet, TTLBoundedSet


def test_ttl_bounded_set_evicts_oldest_items_when_full():
    cache = TTLBoundedSet(max_size=2, ttl_seconds=60)

    cache.add("event-1")
    cache.add("event-2")
    cache.add("event-3")

    assert "event-1" not in cache
    assert "event-2" in cache
    assert "event-3" in cache
    assert len(cache) == 2


def test_try_add_returns_true_for_new_item():
    cache = TTLBoundedSet(max_size=10, ttl_seconds=60)
    assert cache.try_add("event-1") is True
    assert "event-1" in cache


def test_try_add_returns_false_for_duplicate():
    cache = TTLBoundedSet(max_size=10, ttl_seconds=60)
    cache.try_add("event-1")
    assert cache.try_add("event-1") is False


def test_try_add_and_add_compose_correctly():
    cache = TTLBoundedSet(max_size=10, ttl_seconds=60)
    cache.add("legacy-1")
    assert cache.try_add("legacy-1") is False
    assert cache.try_add("fresh-1") is True




def test_ttl_bounded_set_expires_items():
    cache = TTLBoundedSet(max_size=10, ttl_seconds=1)
    cache.add("event-1")

    cache._items["event-1"] -= 2

    assert "event-1" not in cache
    assert len(cache) == 0


def test_persistent_set_survives_recreation(tmp_path):
    db_path = tmp_path / "processed_events.sqlite3"

    cache = PersistentTTLBoundedSet(db_path, max_size=10, ttl_seconds=60)
    assert cache.try_add("message-1") is True
    cache.close()

    recreated = PersistentTTLBoundedSet(db_path, max_size=10, ttl_seconds=60)
    assert recreated.try_add("message-1") is False
    assert "message-1" in recreated
    recreated.close()


def test_persistent_set_evicts_oldest_items_when_full(tmp_path):
    cache = PersistentTTLBoundedSet(tmp_path / "processed_events.sqlite3", max_size=2, ttl_seconds=60)

    cache.add("event-1")
    cache.add("event-2")
    cache.add("event-3")

    assert "event-1" not in cache
    assert "event-2" in cache
    assert "event-3" in cache
    assert len(cache) == 2
    cache.close()


def test_persistent_set_expires_items(tmp_path):
    db_path = tmp_path / "processed_events.sqlite3"
    cache = PersistentTTLBoundedSet(db_path, max_size=10, ttl_seconds=1)
    cache.add("event-1")
    cache.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET created_at = ? WHERE item = ?",
            (time.time() - 2, "event-1"),
        )

    recreated = PersistentTTLBoundedSet(db_path, max_size=10, ttl_seconds=1)
    assert "event-1" not in recreated
    assert len(recreated) == 0
    recreated.close()
