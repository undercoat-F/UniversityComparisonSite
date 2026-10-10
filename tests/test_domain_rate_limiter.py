import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock

from crawler.distributed.domain_rate_limiter import (
    KEY_PREFIX,
    LocalDomainRateLimiter,
    ValkeyDomainRateLimiter,
)

try:  # Lua スクリプトを実際に動かして検証する（未インストールの環境ではスキップ）
    import fakeredis
    import lupa  # noqa: F401
except ImportError:  # pragma: no cover
    fakeredis = None


class TestLocalDomainRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_reservations_for_the_same_domain_are_spaced_by_delay(self):
        limiter = LocalDomainRateLimiter()

        waits = [await limiter.reserve("a.edu", 2.0) for _ in range(3)]

        self.assertAlmostEqual(waits[0], 0.0, places=2)
        self.assertAlmostEqual(waits[1], 2.0, places=2)
        self.assertAlmostEqual(waits[2], 4.0, places=2)

    async def test_domains_are_independent_and_zero_delay_is_not_reserved(self):
        limiter = LocalDomainRateLimiter()

        await limiter.reserve("a.edu", 2.0)
        self.assertAlmostEqual(await limiter.reserve("b.edu", 2.0), 0.0, places=2)
        self.assertEqual(await limiter.reserve("c.edu", 0.0), 0.0)
        self.assertEqual(await limiter.reserve("c.edu", 0.0), 0.0)

    async def test_mark_started_pushes_next_slot_from_actual_start(self):
        # 待ち明けが遅れて実際の開始が予約より後になっても、次の枠は実際の開始から Crawl-delay 後になる
        limiter = LocalDomainRateLimiter()
        await limiter.reserve("a.edu", 1.0)
        time.sleep(0.2)  # 実際の開始が 0.2 秒遅れた
        await limiter.mark_started("a.edu", 1.0)

        self.assertAlmostEqual(await limiter.reserve("a.edu", 1.0), 1.0, delta=0.05)


@unittest.skipIf(fakeredis is None, "fakeredis[lua] is not installed")
class TestValkeyDomainRateLimiter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.limiter = ValkeyDomainRateLimiter(self.client)

    async def test_reservations_for_the_same_domain_are_spaced_by_delay(self):
        first = await self.limiter.reserve("a.edu", 1.0)
        second = await self.limiter.reserve("a.edu", 1.0)
        other = await self.limiter.reserve("b.edu", 1.0)

        self.assertAlmostEqual(first, 0.0, delta=0.05)
        self.assertAlmostEqual(second, 1.0, delta=0.05)
        self.assertAlmostEqual(other, 0.0, delta=0.05)

    def test_concurrent_reservations_from_multiple_workers_do_not_overlap(self):
        # 別々の Worker（別々の接続）が同時に予約しても、待ち時間は Crawl-delay ずつずれる
        server = fakeredis.FakeServer()
        limiters = [ValkeyDomainRateLimiter(fakeredis.FakeRedis(server=server, decode_responses=True)) for _ in range(5)]
        waits = []
        lock = threading.Lock()

        def reserve(limiter):
            wait = limiter.reserve_blocking("a.edu", 1.0)
            with lock:
                waits.append(wait)

        threads = [threading.Thread(target=reserve, args=(limiter,)) for limiter in limiters]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for expected, actual in zip([0, 1, 2, 3, 4], sorted(waits)):
            self.assertAlmostEqual(actual, expected, delta=0.1)

    async def test_key_expires_after_the_reserved_slot(self):
        await self.limiter.reserve("a.edu", 2.0)

        ttl_ms = self.client.pttl(KEY_PREFIX + "a.edu")
        self.assertGreater(ttl_ms, 2000)
        self.assertLessEqual(ttl_ms, 2000 + 10 * 60 * 1000)

    async def test_mark_started_pushes_next_slot_from_actual_start(self):
        await self.limiter.reserve("a.edu", 1.0)
        time.sleep(0.2)
        await self.limiter.mark_started("a.edu", 1.0)

        self.assertAlmostEqual(await self.limiter.reserve("a.edu", 1.0), 1.0, delta=0.05)

    async def test_mark_started_does_not_shorten_an_existing_reservation(self):
        await self.limiter.reserve("a.edu", 1.0)
        await self.limiter.reserve("a.edu", 1.0)   # 次の枠まで予約済み（2秒先まで埋まっている）
        await self.limiter.mark_started("a.edu", 1.0)

        self.assertAlmostEqual(await self.limiter.reserve("a.edu", 1.0), 2.0, delta=0.05)


class TestValkeyFallback(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_worker_local_reservation_when_valkey_fails(self):
        client = MagicMock()
        client.register_script.return_value = MagicMock(side_effect=ConnectionError("down"))
        limiter = ValkeyDomainRateLimiter(client)

        first = await limiter.reserve("a.edu", 1.0)
        second = await limiter.reserve("a.edu", 1.0)

        self.assertAlmostEqual(first, 0.0, delta=0.05)
        self.assertAlmostEqual(second, 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
