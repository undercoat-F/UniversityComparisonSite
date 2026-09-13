from __future__ import annotations

import json
import os

try:
    import boto3
except ImportError:  # pragma: no cover - boto3 is optional until wired in
    boto3 = None

DEFAULT_MAX_DELAY_SECONDS = 900  # SQSのDelaySecondsは最大900秒(15分)


class TaskQueue:
    """SQS FIFOキューへのURLタスク投入(Producer)・受信(Consumer)を担う。"""

    def __init__(self, queue_url: str, region_name: str = "") -> None:
        if boto3 is None:
            raise RuntimeError("boto3 package is not installed. Install boto3 (pip install boto3).")
        if not queue_url:
            raise ValueError("queue_url is required")
        client_kwargs = {"region_name": region_name} if region_name else {}
        self._client = boto3.client("sqs", **client_kwargs)
        self.queue_url = queue_url

    def send_task(
        self,
        *,
        run_id: int,
        url: str,
        depth: int,
        domain: str,
        discovered_from: str = "",
        delay_seconds: int = 0,
    ) -> str:
        """URLタスクを送信する。戻り値はSQSのMessageId。"""
        body = json.dumps(
            {
                "run_id": run_id,
                "url": url,
                "depth": depth,
                "domain": domain,
                "discovered_from": discovered_from,
            }
        )
        response = self._client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=body,
            MessageGroupId=domain,
            MessageDeduplicationId=f"{domain}:{url}:{depth}",
            DelaySeconds=max(0, min(DEFAULT_MAX_DELAY_SECONDS, int(delay_seconds))),
        )
        return response["MessageId"]

    def receive_tasks(self, max_messages: int = 10, wait_time_seconds: int = 20) -> list[dict]:
        """URLタスクを受信する(ロングポーリング)。"""
        response = self._client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max(1, min(10, int(max_messages))),
            WaitTimeSeconds=max(0, min(20, int(wait_time_seconds))),
        )
        tasks = []
        for message in response.get("Messages", []):
            receipt_handle = message["ReceiptHandle"]
            try:
                body = json.loads(message["Body"])
                tasks.append(
                    {
                        "receipt_handle": receipt_handle,
                        "run_id": body["run_id"],
                        "url": body["url"],
                        "depth": body["depth"],
                        "domain": body["domain"],
                        "discovered_from": body.get("discovered_from", ""),
                    }
                )
            except Exception:
                self.delete_task(receipt_handle)
        return tasks

    def delete_task(self, receipt_handle: str) -> None:
        """処理済みタスクをキューから削除する。"""
        self._client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)

    def get_message_counts(self) -> dict[str, int]:
        """可視・処理中・遅延中の概算メッセージ数を取得する。"""
        response = self._client.get_queue_attributes(
            QueueUrl=self.queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            ],
        )
        attributes = response.get("Attributes", {})
        return {
            "visible": int(attributes.get("ApproximateNumberOfMessages", 0)),
            "in_flight": int(attributes.get("ApproximateNumberOfMessagesNotVisible", 0)),
            "delayed": int(attributes.get("ApproximateNumberOfMessagesDelayed", 0)),
        }


_default_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    """環境変数(SQS_QUEUE_URL / AWS_REGION)から既定のTaskQueueを取得する。"""
    global _default_queue
    if _default_queue is None:
        queue_url = os.getenv("SQS_QUEUE_URL", "").strip()
        region_name = os.getenv("AWS_REGION", "").strip()
        _default_queue = TaskQueue(queue_url=queue_url, region_name=region_name)
    return _default_queue
