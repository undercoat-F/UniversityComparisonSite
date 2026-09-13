#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dataclass.dataclass import CrawlAttempt
from crawler.distributed.site_cache import DomainSiteCache
from crawler.distributed.worker import _process_message


class TestWorkerE2E(unittest.IsolatedAsyncioTestCase):
    async def test_message_lifecycle_and_redis_mark_seen(self):
        """
        今回の疎通テストを再現する:
        1. SQS にメッセージを投入する
        2. worker が受信・処理する
        3. 成功時にメッセージが削除される
        4. Redis に seen: キーが残る(重複排除が動いた)
        """
        # --- 1. SQS 側の振る舞いを模す ---
        fake_client = MagicMock()
        fake_client.receive_message.return_value = {
            "Messages": [
                {
                    "ReceiptHandle": "handle-1",
                    "Body": '{"run_id": 123, "url": "https://example.com/", "depth": 0, "domain": "example.com", "discovered_from": ""}',
                }
            ]
        }
        fake_client.send_message.return_value = {"MessageId": "fake-id"}
        fake_client.delete_message.return_value = {}

        with patch("crawler.distributed.sqs_queue.boto3") as fake_boto3:
            fake_boto3.client.return_value = fake_client
            from crawler.distributed.sqs_queue import TaskQueue
            task_queue = TaskQueue(queue_url="https://sqs.example/fake-queue.fifo")

        # --- 2. Redis 側の振る舞いを模す ---
        fake_redis_client = MagicMock()
        fake_redis_client.set.return_value = True
        fake_redis_client.exists.return_value = 1
        fake_redis_client.keys.return_value = ["seen:abc123"]

        with patch("crawler.distributed.dedup_store.redis") as fake_redis_module:
            fake_redis_module.from_url.return_value = fake_redis_client
            from crawler.distributed.dedup_store import DedupStore
            dedup_store = DedupStore(redis_url="redis://localhost:6379/0")

        # --- 3. worker の内部処理をモック化 ---
        site_cache = DomainSiteCache()
        message = task_queue.receive_tasks()[0]

        async def fake_run_url_task(site, task, session):
            # 実際のクロール処理を模す: 新規リンクを1件 enqueue する
            site.enqueue(url="https://example.com/about", depth=1, discovered_from=task.url)
            return CrawlAttempt(url=task.url, ok=True)

        with patch("crawler.distributed.worker.ensure_robots", new=AsyncMock()), patch(
            "crawler.distributed.worker.run_url_task", new=AsyncMock(side_effect=fake_run_url_task)
        ):
            # --- 4. 実際に worker の1メッセージ処理を実行 ---
            await _process_message(
                message,
                task_queue=task_queue,
                site_cache=site_cache,
                dedup_store=dedup_store,
                queue_logger=None,
                worker_id="test-worker",
                active_run_ids=set(),
                session=MagicMock(),
                max_depth=5,
            )

        # --- 5. 検証: ここが「疎通確認」に相当する ---
        # 5-1. SQS からメッセージが削除されたか
        fake_client.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.example/fake-queue.fifo", ReceiptHandle="handle-1"
        )

        # 5-2. 新規リンクが SQS に転送されたか
        fake_client.send_message.assert_called_once()
        _, send_kwargs = fake_client.send_message.call_args
        self.assertEqual(send_kwargs["MessageGroupId"], "example.com")
        self.assertIn("https://example.com/about", send_kwargs["MessageBody"])

        # 5-3. Redis に重複キーが登録されたか
        fake_redis_client.set.assert_called()
        _, redis_kwargs = fake_redis_client.set.call_args
        self.assertTrue(redis_kwargs["nx"])
        self.assertTrue(redis_kwargs["ex"])

        print("[E2E] SQS 投入→worker 受信→Redis 登録→SQS 削除 の流れを再現できました")


if __name__ == "__main__":
    unittest.main()

#外部接続なしのモック確認　何を確認すべきか　を記したコード
