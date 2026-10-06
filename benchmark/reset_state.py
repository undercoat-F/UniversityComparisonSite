"""計測の前後に、前回の計測で残った Valkey・SQS の状態を片付ける。

片付ける対象:
  Valkey  seen:<sha1(URL)>       重複排除の記録。残っていると次の計測で「処理済み」と判定され、クロールされない
          crawl:next:<ドメイン>   Crawl-delay の予約。残っていると次の計測の最初のアクセスが待たされる
  SQS     メインキュー             中断した計測のタスク。残っていると次の計測で処理されてしまう
          DLQ                     失敗したタスク。前回の失敗が次の計測の結果に混ざらないよう空にする

Valkey は本番と共用のため、Mock の URL から計算したキーだけを1件ずつ削除する（全消去はしない）。
SQS も本番と共用のため、キューを空にする前に本番のクロールが動いていないことを確認すること。

ElastiCache（Valkey）は VPC 内からしか接続できないため、Worker と同じ EC2 上で実行する。
REDIS_URL / SQS_QUEUE_URL / AWS_REGION は Worker と同じ .env から読む。

使い方（リポジトリのルートで実行。既定は確認のみで、--execute を付けたときだけ削除する）:
  python -m benchmark.reset_state --pages benchmark/results/A-inspect/pages.csv
  python -m benchmark.reset_state --scenario benchmark/mock/scenarios/A_scaling.yaml --execute
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

RATE_LIMIT_KEY_PREFIX = "crawl:next:"  # crawler/distributed/domain_rate_limiter.py と同じ
DEDUP_KEY_PREFIX = "seen:"             # crawler/distributed/dedup_store.py と同じ


def dedup_key(url: str) -> str:
    return DEDUP_KEY_PREFIX + hashlib.sha1(url.encode("utf-8")).hexdigest()


def urls_from_pages_csv(path: Path) -> set[str]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["url"] for row in csv.DictReader(f)}


def urls_from_scenario(path: Path) -> set[str]:
    from benchmark.mock.scenario import load_scenario  # pyyaml が必要なため必要時のみ読み込む

    scenario = load_scenario(path)
    return {scenario.url(host, page.path) for host, domain in scenario.domains.items() for page in domain.pages.values()}


def urls_from_access_log(path: Path, scheme: str = "https") -> set[str]:
    """前回の計測で実際にアクセスされた URL（シナリオにない URL も含めて消すため）。"""
    urls = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                if record["path"] not in ("/robots.txt", "/sitemap.xml"):
                    urls.add(f"{scheme}://{record['host']}{record['path']}")
    return urls


def build_targets(urls: Iterable[str]) -> tuple[set[str], set[str]]:
    """(重複排除キー, Crawl-delay 予約キー) を返す。"""
    urls = set(urls)
    hosts = {urlparse(u).netloc for u in urls}
    # 起点 URL は seed テーブルの書き方によって末尾の "/" の有無が変わるため両方消す
    urls |= {f"https://{h}" for h in hosts} | {f"https://{h}/" for h in hosts}
    return {dedup_key(u) for u in urls}, {RATE_LIMIT_KEY_PREFIX + h for h in hosts}


def count_existing(client, keys: list[str]) -> int:
    # ElastiCache Serverless はクラスタモードのため、複数キーをまとめた EXISTS/DEL は使わず1件ずつ送る
    pipe = client.pipeline(transaction=False)
    for key in keys:
        pipe.exists(key)
    return sum(int(x) for x in pipe.execute())


def delete_keys(client, keys: list[str]) -> int:
    pipe = client.pipeline(transaction=False)
    for key in keys:
        pipe.delete(key)
    return sum(int(x) for x in pipe.execute())


def dlq_url(sqs, queue_url: str) -> str | None:
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["RedrivePolicy"]).get("Attributes", {})
    policy = attrs.get("RedrivePolicy")
    if not policy:
        return None
    dlq_name = json.loads(policy)["deadLetterTargetArn"].rsplit(":", 1)[-1]
    return sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]


def queue_counts(sqs, queue_url: str) -> dict[str, int]:
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    ).get("Attributes", {})
    return {
        "visible": int(attrs.get("ApproximateNumberOfMessages", 0)),
        "in_flight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        "delayed": int(attrs.get("ApproximateNumberOfMessagesDelayed", 0)),
    }


def reset_valkey(client, urls: set[str], execute: bool) -> None:
    dedup_keys, rate_keys = build_targets(urls)
    keys = sorted(dedup_keys) + sorted(rate_keys)
    existing_dedup = count_existing(client, sorted(dedup_keys))
    existing_rate = count_existing(client, sorted(rate_keys))
    print(f"[VALKEY] dedup keys: {existing_dedup} exist / {len(dedup_keys)} checked")
    print(f"[VALKEY] crawl-delay keys: {existing_rate} exist / {len(rate_keys)} checked")
    if not execute:
        return
    deleted = delete_keys(client, keys)
    print(f"[VALKEY] deleted {deleted} keys")


def reset_sqs(sqs, queue_url: str, execute: bool) -> None:
    targets = [("main", queue_url)]
    dlq = dlq_url(sqs, queue_url)
    if dlq:
        targets.append(("dlq", dlq))
    for name, url in targets:
        counts = queue_counts(sqs, url)
        print(f"[SQS] {name}: {counts}  ({url})")
        if execute and any(counts.values()):
            try:
                sqs.purge_queue(QueueUrl=url)
                print(f"[SQS] {name}: purge requested (completes within 60 seconds)")
            except sqs.exceptions.PurgeQueueInProgress:
                print(f"[SQS] {name}: a purge is already in progress (only one purge per 60 seconds)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Valkey/SQS state left by a previous benchmark run")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pages", help="pages.csv written by inspect_scenario.py --out or the mock server")
    source.add_argument("--scenario", help="scenario YAML (requires pyyaml)")
    parser.add_argument("--access-log", action="append", default=[],
                        help="access.jsonl of previous runs; URLs found there are also cleared (repeatable)")
    parser.add_argument("--skip-valkey", action="store_true")
    parser.add_argument("--skip-sqs", action="store_true")
    parser.add_argument("--execute", action="store_true", help="actually delete (default: only show counts)")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(encoding="utf-8-sig")
    except ImportError:
        pass

    urls = urls_from_pages_csv(Path(args.pages)) if args.pages else urls_from_scenario(Path(args.scenario))
    for log in args.access_log:
        urls |= urls_from_access_log(Path(log))
    hosts = sorted({urlparse(u).netloc for u in urls})
    print(f"target: {len(hosts)} domains, {len(urls)} URLs (e.g. {hosts[0] if hosts else '-'})")
    if any(not h.endswith(".bench.internal") for h in hosts):
        sys.exit("refusing: some hosts are not *.bench.internal; this script only clears benchmark state")

    if not args.execute:
        print("dry run: nothing is deleted. Add --execute to delete.")

    if not args.skip_valkey:
        import redis

        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            sys.exit("REDIS_URL is not set")
        reset_valkey(redis.from_url(redis_url, decode_responses=True), urls, args.execute)

    if not args.skip_sqs:
        import boto3

        queue_url = os.getenv("SQS_QUEUE_URL", "").strip()
        if not queue_url:
            sys.exit("SQS_QUEUE_URL is not set")
        region = os.getenv("AWS_REGION", "").strip()
        reset_sqs(boto3.client("sqs", **({"region_name": region} if region else {})), queue_url, args.execute)

    print("reminder: restart the worker containers before the next run "
          "(each worker keeps visited URLs and run_id of the previous run in memory)")


if __name__ == "__main__":
    main()
