import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from crawler.distributed.worker import run_worker


class TestWorkerShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_worker_records_stopped_for_active_run_on_cancellation(self):
        task_queue = MagicMock()
        task_queue.queue_url = "https://sqs.example/crawl-tasks.fifo"
        task_queue.receive_tasks.side_effect = [
            [{"run_id": 123, "url": "https://example.edu/", "depth": 0, "domain": "example.edu", "discovered_from": ""}],
            KeyboardInterrupt(),
        ]
        queue_logger = MagicMock()

        async def process_message(*args, **kwargs):
            kwargs["active_run_ids"].add(123)

        with patch("crawler.distributed.worker.get_task_queue", return_value=task_queue), patch(
            "crawler.distributed.worker._get_dedup_store_or_none", return_value=None
        ), patch("crawler.distributed.worker._queue_log_store_or_none", return_value=queue_logger), patch(
            "crawler.distributed.worker._worker_id", return_value="test-worker"
        ), patch("crawler.distributed.worker.stop_resource_monitor"), patch(
            "crawler.distributed.worker._process_message", new=AsyncMock(side_effect=process_message)
        ):
            with self.assertRaises(KeyboardInterrupt):
                await run_worker()

        queue_logger.close.assert_called_once()
        queue_logger.update_worker_run.assert_called_once_with(123, "test-worker", status="stopped")

    async def test_worker_records_failed_for_active_run_on_unexpected_error(self):
        task_queue = MagicMock()
        task_queue.queue_url = "https://sqs.example/crawl-tasks.fifo"
        task_queue.receive_tasks.side_effect = [
            [{"run_id": 123, "url": "https://example.edu/", "depth": 0, "domain": "example.edu", "discovered_from": ""}],
            RuntimeError("SQS unavailable"),
        ]
        queue_logger = MagicMock()

        async def process_message(*args, **kwargs):
            kwargs["active_run_ids"].add(123)

        with patch("crawler.distributed.worker.get_task_queue", return_value=task_queue), patch(
            "crawler.distributed.worker._get_dedup_store_or_none", return_value=None
        ), patch("crawler.distributed.worker._queue_log_store_or_none", return_value=queue_logger), patch(
            "crawler.distributed.worker._worker_id", return_value="test-worker"
        ), patch("crawler.distributed.worker.stop_resource_monitor"), patch(
            "crawler.distributed.worker._process_message", new=AsyncMock(side_effect=process_message)
        ):
            with self.assertRaisesRegex(RuntimeError, "SQS unavailable"):
                await run_worker()

        queue_logger.close.assert_called_once()
        queue_logger.update_worker_run.assert_called_once_with(123, "test-worker", status="failed")


if __name__ == "__main__":
    unittest.main()
