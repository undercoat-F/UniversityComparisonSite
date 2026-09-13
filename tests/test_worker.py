#!/usr/bin/env python
# -*- coding: utf-8 -*-

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


if __name__ == "__main__":
    unittest.main()
