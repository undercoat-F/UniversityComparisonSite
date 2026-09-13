#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import unittest
from unittest.mock import patch

from crawler.distributed.dedup_store import DedupStore


class _FakeRedisClient:
    """redis-py の set(nx=,ex=)/exists/delete を模した簡易インメモリスタブ。"""

    def __init__(self) -> None:
        self._store: dict[str, float | None] = {}

    def set(self, key, value, nx=False, ex=None):
        expires_at = time.time() + ex if ex else None
        if nx and key in self._store:
            return None
        self._store[key] = expires_at
        return True

    def exists(self, key):
        return 1 if key in self._store else 0

    def delete(self, key):
        self._store.pop(key, None)

    def close(self):
        self._store.clear()


class TestDedupStore(unittest.TestCase):
    def _make_store(self) -> DedupStore:
        with patch("crawler.distributed.dedup_store.redis") as fake_redis_module:
            fake_redis_module.from_url.return_value = _FakeRedisClient()
            return DedupStore(redis_url="redis://localhost:6379/0", ttl_seconds=60)

    def test_mark_seen_returns_true_only_once(self):
        store = self._make_store()
        self.assertTrue(store.mark_seen("https://example.edu/a"))
        self.assertFalse(store.mark_seen("https://example.edu/a"))

    def test_different_urls_are_independent(self):
        store = self._make_store()
        self.assertTrue(store.mark_seen("https://example.edu/a"))
        self.assertTrue(store.mark_seen("https://example.edu/b"))

    def test_is_seen_does_not_register(self):
        store = self._make_store()
        self.assertFalse(store.is_seen("https://example.edu/a"))
        self.assertFalse(store.mark_seen("https://example.edu/a") is False)  # 初回はTrue
        self.assertTrue(store.is_seen("https://example.edu/a"))

    def test_forget_allows_remarking(self):
        store = self._make_store()
        store.mark_seen("https://example.edu/a")
        store.forget("https://example.edu/a")
        self.assertTrue(store.mark_seen("https://example.edu/a"))


if __name__ == "__main__":
    unittest.main()
