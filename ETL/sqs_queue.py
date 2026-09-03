from __future__ import annotations

import json
import os

try:
    import boto3
except ImportError:  # pragma: no cover - boto3 is optional until wired in
    boto3 = None

DEFAULT_MAX_DELAY_SECONDS = 900  # SQSのDelaySecondsは最大900秒(15分)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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
        url: str,
        depth: int,
        domain: str,
        discovered_from: str = "",
        delay_seconds: int = 0,
    ) -> str:
        """URLタスクを送信する。戻り値はSQSのMessageId。

        domain を MessageGroupId に使うことで、同一ドメインのタスクはSQS側で
        常に1件ずつ順番に処理される(crawl-delay違反の防止)。
        delay_seconds はドメインのcrawl-delayをそのまま渡すことで、
        次のタスクが早く見えすぎないように遅延させる。
        """
        body = json.dumps(
            {
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
        """URLタスクを受信する(ロングポーリング)。

        戻り値は [{"receipt_handle": str, "url": str, "depth": int, "domain": str,
        "discovered_from": str}, ...] のリスト。JSONとしてパースできないメッセージは
        壊れたメッセージとみなし、その場で削除して読み飛ばす(キューに残り続けないように)。
        """
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
        """処理済みタスクをキューから削除する。呼ばなければvisibility timeout後に再配信される。"""
        self._client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)


_default_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    """環境変数(SQS_QUEUE_URL / AWS_REGION)から既定のTaskQueueを取得する。"""
    global _default_queue
    if _default_queue is None:
        queue_url = os.getenv("SQS_QUEUE_URL", "").strip()
        region_name = os.getenv("AWS_REGION", "").strip()
        _default_queue = TaskQueue(queue_url=queue_url, region_name=region_name)
    return _default_queue
