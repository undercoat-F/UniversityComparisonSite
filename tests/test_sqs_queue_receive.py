#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import MagicMock, patch

from crawler.distributed.sqs_queue import TaskQueue


class TestTaskQueueReceiveDelete(unittest.TestCase):
    def _make_queue(self, fake_client=None):
        fake_client = fake_client or MagicMock()
        with patch("crawler.distributed.sqs_queue.boto3") as fake_boto3_module:
            fake_boto3_module.client.return_value = fake_client
            queue = TaskQueue(queue_url="https://sqs.example/fake-queue.fifo")
        return queue, fake_client

    def test_receive_tasks_parses_messages(self):
        fake_client = MagicMock()
        fake_client.receive_message.return_value = {
            "Messages": [
                {
                    "ReceiptHandle": "handle-1",
                    "Body": '{"run_id": 123, "url": "https://example.edu/a", "depth": 1, "domain": "example.edu", "discovered_from": "https://example.edu/"}',
                }
            ]
        }
        queue, fake_client = self._make_queue(fake_client)

        tasks = queue.receive_tasks(max_messages=5, wait_time_seconds=10)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["url"], "https://example.edu/a")
        self.assertEqual(tasks[0]["run_id"], 123)
        self.assertEqual(tasks[0]["depth"], 1)
        self.assertEqual(tasks[0]["domain"], "example.edu")
        self.assertEqual(tasks[0]["receipt_handle"], "handle-1")
        _, kwargs = fake_client.receive_message.call_args
        self.assertEqual(kwargs["QueueUrl"], "https://sqs.example/fake-queue.fifo")
        self.assertEqual(kwargs["MaxNumberOfMessages"], 5)

    def test_receive_tasks_returns_empty_list_when_no_messages(self):
        fake_client = MagicMock()
        fake_client.receive_message.return_value = {}
        queue, _ = self._make_queue(fake_client)

        self.assertEqual(queue.receive_tasks(), [])

    def test_receive_tasks_deletes_and_skips_malformed_message(self):
        fake_client = MagicMock()
        fake_client.receive_message.return_value = {
            "Messages": [{"ReceiptHandle": "broken-handle", "Body": "not-json"}]
        }
        queue, fake_client = self._make_queue(fake_client)

        tasks = queue.receive_tasks()

        self.assertEqual(tasks, [])
        fake_client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.example/fake-queue.fifo", ReceiptHandle="broken-handle"
        )

    def test_delete_task_calls_delete_message(self):
        queue, fake_client = self._make_queue()

        queue.delete_task("handle-1")

        fake_client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.example/fake-queue.fifo", ReceiptHandle="handle-1"
        )


if __name__ == "__main__":
    unittest.main()
