from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from crawler.distributed.sqs_queue import get_task_queue
from ControlPlane.queue_log import QueueLogStore

load_dotenv(encoding="utf-8-sig")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _queue_log_store() -> QueueLogStore:
    dsn = os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
    if not dsn:
        raise RuntimeError("PARENT_DB_OWNER_CONNECTION is required")
    return QueueLogStore(
        db_path="",
        schema_path=os.getenv("QUEUE_LOG_SCHEMA_PATH", os.path.join("ControlPlane", "queue_log_schema.sql")),
        pg_dsn=dsn,
        batch_size=_env_int("QUEUE_LOG_BATCH_SIZE", 200),
        failures_only=False,
    )


def is_run_idle(run_id: int, *, task_queue, queue_logger) -> bool:
    """SQSとDBの両方に未処理タスクがなければTrueを返す。"""
    message_counts = task_queue.get_message_counts()
    unfinished_tasks = queue_logger.count_unfinished_tasks(run_id)
    print(
        f"[RUN_MONITOR] run_id={run_id} visible={message_counts['visible']} "
        f"in_flight={message_counts['in_flight']} delayed={message_counts['delayed']} "
        f"db_unfinished={unfinished_tasks}",
        flush=True,
    )
    if any(message_counts.values()) or unfinished_tasks:
        return False
    return True


def monitor_run(run_id: int, *, interval_sec: int = 30, idle_checks_required: int = 2) -> None:
    """連続したidle確認後に完了にするため、SQSの概算値の一時的なズレを避けられる。"""
    task_queue = get_task_queue()
    queue_logger = _queue_log_store()
    idle_checks = 0
    try:
        while True:
            if is_run_idle(run_id, task_queue=task_queue, queue_logger=queue_logger):
                idle_checks += 1
                if idle_checks >= idle_checks_required:
                    queue_logger.finish_run(run_id, status="completed", notes="SQS and queue state are idle")
                    print(f"[RUN_MONITOR] completed run_id={run_id}", flush=True)
                    return
                print(
                    f"[RUN_MONITOR] idle confirmation {idle_checks}/{idle_checks_required}",
                    flush=True,
                )
            else:
                idle_checks = 0
            time.sleep(interval_sec)
    finally:
        queue_logger.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor a distributed crawl run and mark it completed when idle.")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--interval-sec", type=int, default=_env_int("RUN_MONITOR_INTERVAL_SEC", 30))
    parser.add_argument("--idle-checks", type=int, default=2)
    args = parser.parse_args()
    monitor_run(args.run_id, interval_sec=max(1, args.interval_sec), idle_checks_required=max(2, args.idle_checks))


if __name__ == "__main__":
    main()
