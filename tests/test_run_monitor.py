import unittest
from unittest.mock import MagicMock, patch

from ControlPlane.run_monitor import is_run_idle, monitor_run


class TestRunMonitor(unittest.TestCase):
    def test_run_is_idle_only_when_sqs_and_db_are_empty(self):
        task_queue = MagicMock()
        task_queue.get_message_counts.return_value = {"visible": 0, "in_flight": 0, "delayed": 0}
        queue_logger = MagicMock()
        queue_logger.count_unfinished_tasks.return_value = 0

        self.assertTrue(is_run_idle(123, task_queue=task_queue, queue_logger=queue_logger))

        task_queue.get_message_counts.return_value = {"visible": 0, "in_flight": 1, "delayed": 0}
        self.assertFalse(is_run_idle(123, task_queue=task_queue, queue_logger=queue_logger))

        task_queue.get_message_counts.return_value = {"visible": 0, "in_flight": 0, "delayed": 0}
        queue_logger.count_unfinished_tasks.return_value = 1
        self.assertFalse(is_run_idle(123, task_queue=task_queue, queue_logger=queue_logger))

    def test_monitor_finishes_only_after_two_consecutive_idle_checks(self):
        task_queue = MagicMock()
        task_queue.get_message_counts.return_value = {"visible": 0, "in_flight": 0, "delayed": 0}
        queue_logger = MagicMock()
        queue_logger.count_unfinished_tasks.return_value = 0

        with patch("ControlPlane.run_monitor.get_task_queue", return_value=task_queue), patch(
            "ControlPlane.run_monitor._queue_log_store", return_value=queue_logger
        ), patch("ControlPlane.run_monitor.time.sleep") as sleep:
            monitor_run(123, interval_sec=1, idle_checks_required=2)

        self.assertEqual(task_queue.get_message_counts.call_count, 2)
        queue_logger.finish_run.assert_called_once_with(
            123, status="completed", notes="SQS and queue state are idle"
        )
        sleep.assert_called_once_with(1)
        queue_logger.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
