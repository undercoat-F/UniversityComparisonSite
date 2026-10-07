#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import MagicMock, patch

from crawler.distributed.sqs_queue import TaskQueue


class TestTaskQueue(unittest.TestCase):
    def _make_queue(self, fake_client=None):
        fake_client = fake_client or MagicMock()
        fake_client.send_message.return_value = {"MessageId": "fake-id"}
        with patch("crawler.distributed.sqs_queue.boto3") as fake_boto3_module:
            fake_boto3_module.client.return_value = fake_client
            queue = TaskQueue(queue_url="https://sqs.example/fake-queue.fifo")
        return queue, fake_client

    def test_send_task_sets_message_group_id_to_domain(self):
        queue, fake_client = self._make_queue()

        message_id = queue.send_task(run_id=123, url="https://example.edu/a", depth=0, domain="example.edu")

        self.assertEqual(message_id, "fake-id")
        _, kwargs = fake_client.send_message.call_args
        self.assertEqual(kwargs["MessageGroupId"], "example.edu")
        self.assertEqual(kwargs["QueueUrl"], "https://sqs.example/fake-queue.fifo")

    def test_send_task_does_not_set_per_message_delay(self):
        # FIFOキューではメッセージ単位の DelaySeconds を指定できない
        queue, fake_client = self._make_queue()

        queue.send_task(run_id=123, url="https://example.edu/a", depth=0, domain="example.edu")

        _, kwargs = fake_client.send_message.call_args
        self.assertNotIn("DelaySeconds", kwargs)

    def test_extend_visibility_clamps_to_sqs_maximum(self):
        queue, fake_client = self._make_queue()

        queue.extend_visibility("handle-1", 100_000)

        _, kwargs = fake_client.change_message_visibility.call_args
        self.assertEqual(kwargs["ReceiptHandle"], "handle-1")
        self.assertEqual(kwargs["VisibilityTimeout"], 43200)

    def test_send_task_deduplication_id_varies_by_depth(self):
        queue, fake_client = self._make_queue()

        queue.send_task(run_id=123, url="https://example.edu/a", depth=0, domain="example.edu")
        queue.send_task(run_id=123, url="https://example.edu/a", depth=1, domain="example.edu")

        first_call_kwargs = fake_client.send_message.call_args_list[0].kwargs
        second_call_kwargs = fake_client.send_message.call_args_list[1].kwargs
        self.assertNotEqual(
            first_call_kwargs["MessageDeduplicationId"], second_call_kwargs["MessageDeduplicationId"]
        )


if __name__ == "__main__":
    unittest.main()
