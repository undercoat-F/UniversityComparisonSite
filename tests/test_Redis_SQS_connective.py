import unittest
from unittest.mock import MagicMock, patch

from crawler.distributed.dedup_store import DedupStore
from crawler.distributed.sqs_queue import TaskQueue


class TestRedisConnective(unittest.TestCase):
    def test_redis_connection(self):
        with patch("crawler.distributed.dedup_store.redis") as fake_redis_module:
            fake_client = MagicMock()
            fake_client.ping.return_value = True
            fake_redis_module.from_url.return_value = fake_client

            store = DedupStore(redis_url="redis://localhost:6379/0")
            self.assertTrue(store._client.ping())


class TestSQSConnective(unittest.TestCase):
    def test_sqs_queue_attributes(self):
        fake_client = MagicMock()
        fake_client.get_queue_attributes.return_value = {
            "Attributes": {
                "QueueArn": "arn:aws:sqs:ap-northeast-1:123456789012:crawl-tasks.fifo",
                "FifoQueue": "true",
            }
        }

        with patch("crawler.distributed.sqs_queue.boto3") as fake_boto3_module:
            fake_boto3_module.client.return_value = fake_client
            queue = TaskQueue(queue_url="https://sqs.example/fake-queue.fifo")
            queue._client = fake_client

            r = queue._client.get_queue_attributes(
                QueueUrl="https://sqs.example/fake-queue.fifo",
                AttributeNames=["QueueArn", "FifoQueue"],
            )
            self.assertEqual(r["Attributes"]["FifoQueue"], "true")

    def test_send_task_does_not_actually_send(self):
        fake_client = MagicMock()
        fake_client.send_message.return_value = {"MessageId": "fake-id"}

        with patch("crawler.distributed.sqs_queue.boto3") as fake_boto3_module:
            fake_boto3_module.client.return_value = fake_client
            queue = TaskQueue(queue_url="https://sqs.example/fake-queue.fifo")

            mid = queue.send_task(run_id=123, url="https://example.com/", depth=0, domain="example.com")
            self.assertEqual(mid, "fake-id")
            fake_client.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()