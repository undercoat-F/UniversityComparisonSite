#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dataclass.dataclass import CrawlAttempt
from crawler.distributed.site_cache import DomainSiteCache
from crawler.distributed.worker import _process_message


class TestProcessMessage(unittest.IsolatedAsyncioTestCase):
    async def test_success_forwards_discovered_tasks_and_deletes_message(self):
        task_queue = MagicMock()
        site_cache = DomainSiteCache()
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
        }

        async def fake_run_url_task(site, task, session):
            site.enqueue(url="https://example.edu/b", depth=1, discovered_from=task.url)
            return CrawlAttempt(url=task.url, ok=True)

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(side_effect=fake_run_url_task)
        ):
            await _process_message(
                message,
                task_queue=task_queue,
                site_cache=site_cache,
                dedup_store=None,
                queue_logger=None,
                worker_id="test-worker",
                active_run_ids=set(),
                session=MagicMock(),
                max_depth=5,
            )

        task_queue.send_task.assert_called_once()
        _, kwargs = task_queue.send_task.call_args
        self.assertEqual(kwargs["url"], "https://example.edu/b")
        self.assertEqual(kwargs["domain"], "example.edu")
        task_queue.delete_task.assert_called_once_with("handle-1")

    async def test_failure_does_not_delete_message(self):
        task_queue = MagicMock()
        site_cache = DomainSiteCache()
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
        }

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await _process_message(
                message,
                task_queue=task_queue,
                site_cache=site_cache,
                dedup_store=None,
                queue_logger=None,
                worker_id="test-worker",
                active_run_ids=set(),
                session=MagicMock(),
                max_depth=5,
            )

        task_queue.delete_task.assert_not_called()
        task_queue.send_task.assert_not_called()

    async def test_reuses_cached_site_state_for_same_domain(self):
        task_queue = MagicMock()
        site_cache = DomainSiteCache()
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
        }

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()) as fake_ensure_robots, patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(return_value=CrawlAttempt(url="x", ok=True))
        ):
            await _process_message(
                message, task_queue=task_queue, site_cache=site_cache, dedup_store=None,
                queue_logger=None, worker_id="test-worker", active_run_ids=set(),
                session=MagicMock(), max_depth=5,
            )
            await _process_message(
                message, task_queue=task_queue, site_cache=site_cache, dedup_store=None,
                queue_logger=None, worker_id="test-worker", active_run_ids=set(),
                session=MagicMock(), max_depth=5,
            )

        self.assertEqual(len(site_cache), 1)
        self.assertEqual(fake_ensure_robots.await_count, 2)

    async def test_extracted_records_go_to_record_sink_instead_of_memory(self):
        task_queue = MagicMock()
        site_cache = DomainSiteCache()
        record_sink = AsyncMock()
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
        }
        captured = {}

        async def fake_run_url_task(site, task, session):
            captured["site"] = site
            await site.add_extracted_record({"url": task.url, "degrees": [{"name": "BSc"}]})
            return CrawlAttempt(url=task.url, ok=True)

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(side_effect=fake_run_url_task)
        ):
            await _process_message(
                message, task_queue=task_queue, site_cache=site_cache, dedup_store=None,
                queue_logger=None, worker_id="test-worker", active_run_ids=set(),
                session=MagicMock(), max_depth=5, record_sink=record_sink,
            )

        record_sink.assert_awaited_once()
        self.assertEqual(record_sink.await_args.args[0], "example.edu")
        self.assertEqual(captured["site"].extracted_records, [])

    async def test_waits_for_reserved_slot_before_fetching(self):
        task_queue = MagicMock()
        site_cache = DomainSiteCache()
        rate_limiter = MagicMock()
        rate_limiter.reserve = AsyncMock(return_value=1.5)
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
        }
        events = []

        async def fake_ensure_robots(site):
            site.crawl_delay = 2.0

        async def fake_sleep(seconds):
            events.append(("sleep", seconds))

        async def fake_run_url_task(site, task, session):
            events.append(("fetch", task.url))
            site.enqueue(url="https://example.edu/b", depth=1, discovered_from=task.url)
            return CrawlAttempt(url=task.url, ok=True)

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock(side_effect=fake_ensure_robots)), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(side_effect=fake_run_url_task)
        ), patch("crawler.distributed.worker.asyncio.sleep", new=fake_sleep):
            await _process_message(
                message, task_queue=task_queue, site_cache=site_cache, dedup_store=None,
                queue_logger=None, worker_id="test-worker", active_run_ids=set(),
                session=MagicMock(), max_depth=5, rate_limiter=rate_limiter,
            )

        rate_limiter.reserve.assert_awaited_once_with("example.edu", 2.0)
        self.assertEqual(events, [("sleep", 1.5), ("fetch", "https://example.edu/a")])
        _, kwargs = task_queue.send_task.call_args
        self.assertNotIn("delay_seconds", kwargs)

    async def test_extends_visibility_when_wait_would_outlive_it(self):
        task_queue = MagicMock()
        rate_limiter = MagicMock()
        rate_limiter.reserve = AsyncMock(return_value=100.0)
        message = {
            "receipt_handle": "handle-1",
            "run_id": 123,
            "url": "https://example.edu/a",
            "depth": 0,
            "domain": "example.edu",
            "discovered_from": "",
            "visible_deadline": time.monotonic() + 180,
        }

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(return_value=CrawlAttempt(url="x", ok=True))
        ), patch("crawler.distributed.worker.asyncio.sleep", new=AsyncMock()):
            await _process_message(
                message, task_queue=task_queue, site_cache=DomainSiteCache(), dedup_store=None,
                queue_logger=None, worker_id="test-worker", active_run_ids=set(),
                session=MagicMock(), max_depth=5, rate_limiter=rate_limiter,
            )

        task_queue.extend_visibility.assert_called_once()
        handle, timeout = task_queue.extend_visibility.call_args.args
        self.assertEqual(handle, "handle-1")
        self.assertGreaterEqual(timeout, 100 + 120)



class TestRunWorkerOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_same_domain_messages_are_processed_one_at_a_time(self):
        import asyncio

        from crawler.distributed.worker import run_worker

        def msg(domain, path):
            return {"run_id": 1, "url": f"https://{domain}{path}", "depth": 0, "domain": domain,
                    "discovered_from": "", "receipt_handle": path}

        task_queue = MagicMock()
        task_queue.queue_url = "https://sqs.example/crawl-tasks.fifo"
        task_queue.receive_tasks.side_effect = [
            [msg("a.edu", "/1"), msg("a.edu", "/2"), msg("b.edu", "/1")],
            KeyboardInterrupt(),
        ]
        active: dict[str, int] = {}
        max_active: dict[str, int] = {}
        overall_max = 0

        async def fake_process(message, **kwargs):
            nonlocal overall_max
            domain = message["domain"]
            active[domain] = active.get(domain, 0) + 1
            max_active[domain] = max(max_active.get(domain, 0), active[domain])
            overall_max = max(overall_max, sum(active.values()))
            await asyncio.sleep(0.01)
            active[domain] -= 1

        with patch("crawler.distributed.worker.get_task_queue", return_value=task_queue), patch(
            "crawler.distributed.worker._get_dedup_store_or_none", return_value=None
        ), patch("crawler.distributed.worker._queue_log_store_or_none", return_value=None), patch(
            "crawler.distributed.worker._record_writer_or_none", return_value=None
        ), patch("crawler.distributed.worker.start_resource_monitor", return_value=None), patch(
            "crawler.distributed.worker._process_message", new=fake_process
        ):
            with self.assertRaises(KeyboardInterrupt):
                await run_worker()

        self.assertEqual(max_active["a.edu"], 1)  # 同じドメインは同時に処理しない
        self.assertEqual(overall_max, 2)          # 別ドメインは並行して処理する


if __name__ == "__main__":
    unittest.main()
