"""計測の完了を待つ。Mock のアクセスログが一定時間増えず、（分散版なら）SQS が空になったら終了する。

使い方（手元の PC、リポジトリのルートで実行）:
  python -m benchmark.wait_done --key ~/UniversityComparisonKey.pem --run-label A-distributed-x2-run1
  python -m benchmark.wait_done --key ~/UniversityComparisonKey.pem --run-label A-legacy-x1-run1 --no-sqs
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from benchmark.distribute_certs import MOCK_ROLE, SSH_USER, find_instances, ssh_base

RESULTS_DIR = "/var/lib/benchmark/results"


def mock_log_lines(key: str, host: str, label: str) -> int:
    result = subprocess.run(
        ["ssh", *ssh_base(key), f"{SSH_USER}@{host}", f"wc -l < {RESULTS_DIR}/{label}/access.jsonl"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"could not read the mock access log: {result.stderr.strip()}")
    return int(result.stdout.strip() or 0)


def sqs_counts(queue_url: str, region: str) -> dict[str, int]:
    import boto3

    attrs = boto3.client("sqs", region_name=region).get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
                        "ApproximateNumberOfMessagesDelayed"],
    )["Attributes"]
    return {k: int(v) for k, v in attrs.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait until a benchmark run has finished")
    parser.add_argument("--key", required=True, help="SSH private key (UniversityComparisonKey.pem)")
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--idle-sec", type=int, default=90, help="finish when the access log has not grown for this long")
    parser.add_argument("--interval-sec", type=int, default=15)
    parser.add_argument("--no-sqs", action="store_true", help="do not check SQS (Legacy)")
    parser.add_argument("--region", default="ap-northeast-1")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(".env", encoding="utf-8-sig")
    except ImportError:
        pass
    queue_url = os.getenv("SQS_QUEUE_URL", "").strip()
    if not args.no_sqs and not queue_url:
        sys.exit("SQS_QUEUE_URL is not set (or pass --no-sqs)")

    key = str(Path(args.key).expanduser())
    mock = next((i for i in find_instances(args.region) if i["role"] == MOCK_ROLE and i["public_ip"]), None)
    if mock is None:
        sys.exit("the mock instance is not running")

    started = time.monotonic()
    last_lines, last_change = -1, time.monotonic()
    while True:
        lines = mock_log_lines(key, mock["public_ip"], args.run_label)
        if lines != last_lines:
            last_lines, last_change = lines, time.monotonic()
        idle = time.monotonic() - last_change
        counts = {} if args.no_sqs else sqs_counts(queue_url, args.region)
        sqs_empty = args.no_sqs or not any(counts.values())
        print(f"[{time.monotonic() - started:6.0f}s] access log {lines} lines (idle {idle:.0f}s)"
              + ("" if args.no_sqs else f" | SQS {counts}"), flush=True)
        if lines > 0 and idle >= args.idle_sec and sqs_empty:
            print(f"done: no new requests for {idle:.0f}s" + ("" if args.no_sqs else " and SQS is empty"))
            return
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    main()
