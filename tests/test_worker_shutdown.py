import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from crawler.distributed.worker import run_worker

MESSAGE = {"run_id": 123, "url": "https://example.edu/", "depth": 0, "domain": "example.edu", "discovered_from": ""}


def _queue(receive):
    task_queue = MagicMock()
    task_queue.queue_url = "https://sqs.example/crawl-tasks.fifo"
    task_queue.receive_tasks.side_effect = receive
    return task_queue


def _patches(task_queue, queue_logger, process_message):
    return [
        patch("crawler.distributed.worker.get_task_queue", return_value=task_queue),
        patch("crawler.distributed.worker._get_dedup_store_or_none", return_value=None),
        patch("crawler.distributed.worker._queue_log_store_or_none", return_value=queue_logger),
        patch("crawler.distributed.worker._record_writer_or_none", return_value=None),
        patch("crawler.distributed.worker._worker_id", return_value="test-worker"),
        patch("crawler.distributed.worker.start_resource_monitor", return_value=None),
        patch("crawler.distributed.worker.stop_resource_monitor"),
        patch("crawler.distributed.worker._process_message", new=AsyncMock(side_effect=process_message)),
    ]


class TestWorkerShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_worker_records_stopped_for_active_run_on_cancellation(self):
        # Ctrl+C や docker stop（SIGTERM）は、run_worker のタスクのキャンセルとして届く
        calls = {"n": 0}

        def receive(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return [dict(MESSAGE)]
            time.sleep(0.01)
            return []

        queue_logger = MagicMock()
        processed = asyncio.Event()

        async def process_message(*args, **kwargs):
            kwargs["active_run_ids"].add(123)
            processed.set()

        patches = _patches(_queue(receive), queue_logger, process_message)
        for p in patches:
            p.start()
        try:
            task = asyncio.create_task(run_worker(max_concurrency=2))
            await asyncio.wait_for(processed.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            for p in patches:
                p.stop()

        queue_logger.close.assert_called_once()
        queue_logger.update_worker_run.assert_called_once_with(123, "test-worker", status="stopped")

    async def test_worker_records_failed_for_active_run_on_unexpected_error(self):
        calls = {"n": 0}

        def receive(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return [dict(MESSAGE)]
            time.sleep(0.05)  # 1件目の処理が先に終わるようにする
            raise RuntimeError("SQS unavailable")

        queue_logger = MagicMock()

        async def process_message(*args, **kwargs):
            kwargs["active_run_ids"].add(123)

        patches = _patches(_queue(receive), queue_logger, process_message)
        for p in patches:
            p.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "SQS unavailable"):
                await asyncio.wait_for(run_worker(max_concurrency=2), timeout=5)
        finally:
            for p in patches:
                p.stop()

        queue_logger.close.assert_called_once()
        queue_logger.update_worker_run.assert_called_once_with(123, "test-worker", status="failed")


if __name__ == "__main__":
    unittest.main()
