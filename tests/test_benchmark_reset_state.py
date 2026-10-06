import json
import unittest
from unittest.mock import MagicMock

from benchmark.reset_state import build_targets, dedup_key, reset_sqs, reset_valkey
from crawler.distributed.dedup_store import _url_key
from crawler.distributed.domain_rate_limiter import KEY_PREFIX


class FakeRedis:
    """pipeline で exists / delete を1件ずつ送る最小限の偽物。"""

    def __init__(self, keys):
        self.store = set(keys)

    def pipeline(self, transaction=False):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def exists(self, key):
        self.ops.append(("exists", key))

    def delete(self, key):
        self.ops.append(("delete", key))

    def execute(self):
        results = []
        for op, key in self.ops:
            present = key in self.client.store
            if op == "delete":
                self.client.store.discard(key)
            results.append(1 if present else 0)
        return results


URLS = {"https://d001.bench.internal/", "https://d001.bench.internal/programs/course-0001"}


class TestResetValkey(unittest.TestCase):
    def test_keys_match_the_worker_formats(self):
        dedup_keys, rate_keys = build_targets(URLS)

        self.assertIn(_url_key("https://d001.bench.internal/programs/course-0001"), dedup_keys)
        self.assertIn(_url_key("https://d001.bench.internal"), dedup_keys)  # 末尾 "/" なしの起点も消す
        self.assertEqual(rate_keys, {KEY_PREFIX + "d001.bench.internal"})

    def test_dry_run_does_not_delete(self):
        client = FakeRedis({dedup_key("https://d001.bench.internal/"), "seen:production"})

        reset_valkey(client, URLS, execute=False)

        self.assertEqual(len(client.store), 2)

    def test_execute_deletes_only_benchmark_keys(self):
        benchmark_keys = {dedup_key(u) for u in URLS} | {KEY_PREFIX + "d001.bench.internal"}
        production_keys = {dedup_key("https://www.ed.ac.uk/"), KEY_PREFIX + "www.ed.ac.uk"}
        client = FakeRedis(benchmark_keys | production_keys)

        reset_valkey(client, URLS, execute=True)

        self.assertEqual(client.store, production_keys)


class TestResetSqs(unittest.TestCase):
    def _sqs(self, main_counts, dlq_counts):
        sqs = MagicMock()
        sqs.exceptions.PurgeQueueInProgress = type("PurgeQueueInProgress", (Exception,), {})
        sqs.get_queue_url.return_value = {"QueueUrl": "https://sqs/dlq.fifo"}

        def attrs(QueueUrl, AttributeNames):
            if AttributeNames == ["RedrivePolicy"]:
                arn = "arn:aws:sqs:ap-northeast-1:123:crawl-tasks-dlq.fifo"
                return {"Attributes": {"RedrivePolicy": json.dumps({"deadLetterTargetArn": arn})}}
            counts = main_counts if QueueUrl == "https://sqs/main.fifo" else dlq_counts
            return {"Attributes": {
                "ApproximateNumberOfMessages": str(counts[0]),
                "ApproximateNumberOfMessagesNotVisible": str(counts[1]),
                "ApproximateNumberOfMessagesDelayed": "0",
            }}

        sqs.get_queue_attributes.side_effect = attrs
        return sqs

    def test_dry_run_does_not_purge(self):
        sqs = self._sqs((5, 1), (2, 0))

        reset_sqs(sqs, "https://sqs/main.fifo", execute=False)

        sqs.purge_queue.assert_not_called()
        sqs.get_queue_url.assert_called_once_with(QueueName="crawl-tasks-dlq.fifo")

    def test_execute_purges_only_non_empty_queues(self):
        sqs = self._sqs((5, 1), (0, 0))

        reset_sqs(sqs, "https://sqs/main.fifo", execute=True)

        sqs.purge_queue.assert_called_once_with(QueueUrl="https://sqs/main.fifo")


if __name__ == "__main__":
    unittest.main()
