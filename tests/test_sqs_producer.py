import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ControlPlane.dispatcher import enqueue_initial_tasks


class TestSqsProducer(unittest.IsolatedAsyncioTestCase):
    async def test_initial_urls_are_deduplicated_before_sqs_send(self):
        dedup_store = MagicMock()
        dedup_store.mark_seen.side_effect = [True, False]
        task_queue = MagicMock()
        queue_logger = MagicMock()
        queue_logger.create_run.return_value = 123

        with patch("ControlPlane.dispatcher.get_dedup_store", return_value=dedup_store), patch(
            "ControlPlane.dispatcher.get_task_queue", return_value=task_queue
        ), patch("ControlPlane.dispatcher.QueueLogStore", return_value=queue_logger), patch(
            "ControlPlane.dispatcher._seed_sitemaps_with_limits", new=AsyncMock(return_value=(0, 0))
        ), patch.dict(
            "os.environ", {"REDIS_URL": "redis://localhost:6379/0", "PARENT_DB_OWNER_CONNECTION": "postgresql://unused"}, clear=False
        ):
            sites = await enqueue_initial_tasks(
                [
                    ("https://example.edu/", 1),
                    ("https://example.edu/", 1),
                ]
            )

        self.assertEqual(len(sites), 1)
        self.assertEqual(task_queue.send_task.call_count, 1)
        _, send_kwargs = task_queue.send_task.call_args
        self.assertEqual(send_kwargs["url"], "https://example.edu/")
        self.assertEqual(send_kwargs["run_id"], 123)
        self.assertEqual(send_kwargs["domain"], "example.edu")
        self.assertEqual(send_kwargs["depth"], 0)
        queue_logger.upsert_queue_state.assert_called_once()

    async def test_task_is_not_removed_when_sqs_send_fails(self):
        dedup_store = MagicMock()
        dedup_store.mark_seen.return_value = True
        task_queue = MagicMock()
        task_queue.send_task.side_effect = RuntimeError("SQS unavailable")
        queue_logger = MagicMock()
        queue_logger.create_run.return_value = 123

        with patch("ControlPlane.dispatcher.get_dedup_store", return_value=dedup_store), patch(
            "ControlPlane.dispatcher.get_task_queue", return_value=task_queue
        ), patch("ControlPlane.dispatcher.QueueLogStore", return_value=queue_logger), patch(
            "ControlPlane.dispatcher._seed_sitemaps_with_limits", new=AsyncMock(return_value=(0, 0))
        ), patch.dict(
            "os.environ", {"REDIS_URL": "redis://localhost:6379/0", "PARENT_DB_OWNER_CONNECTION": "postgresql://unused"}, clear=False
        ):
            with self.assertRaisesRegex(RuntimeError, "SQS unavailable"):
                await enqueue_initial_tasks([("https://example.edu/", 1)])

        task_queue.send_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
