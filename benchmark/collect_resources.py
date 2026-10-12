"""計測が終わったら、各インスタンスの CPU・メモリの記録（etl_resource_log_*.csv）を結果フォルダに回収する。

Legacy と分散版の Worker は、実行中の CPU・メモリを 5 秒ごとに log/etl_resource_log_<時刻>.csv に記録している
（ControlPlane/resource_recorder.py、Legacy は ETL/resource_recorder.py）。
各インスタンスで最も新しいファイルを <run-dir>/resources/<インスタンス名>.csv として保存し、
benchmark.analysis.report が CPU avg / RSS max として集計する。

Worker は計測ごとにコンテナを起動し直す（手順書）ため、最も新しいファイルがその計測の記録になる。

使い方（手元の PC、リポジトリのルートで実行）:
  python -m benchmark.collect_resources --key ~/UniversityComparisonKey.pem --run-dir benchmark/results/A-distributed-x2-run1 --role worker
  python -m benchmark.collect_resources --key ~/UniversityComparisonKey.pem --run-dir benchmark/results/A-legacy-x1-run1 --role legacy
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from benchmark.distribute_certs import SSH_USER, find_instances, ssh_base

# ~/bench-app（分散版）と ~/legacy-app（Legacy）の log/ を探す
REMOTE_GLOB = "~/bench-app/log/etl_resource_log_*.csv ~/legacy-app/log/etl_resource_log_*.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect resource logs from crawler instances")
    parser.add_argument("--key", required=True, help="SSH private key (UniversityComparisonKey.pem)")
    parser.add_argument("--run-dir", required=True, help="benchmark/results/<run-label>")
    parser.add_argument("--role", required=True, choices=["worker", "legacy"])
    parser.add_argument("--region", default="ap-northeast-1")
    args = parser.parse_args()

    key = str(Path(args.key).expanduser())
    out = Path(args.run_dir) / "resources"
    out.mkdir(parents=True, exist_ok=True)
    collected = 0
    for instance in find_instances(args.region):
        if instance["role"] != args.role:
            continue
        if instance["state"] != "running" or not instance["public_ip"]:
            print(f"skip: {instance['name']} ({instance['state']})")
            continue
        host = f"{SSH_USER}@{instance['public_ip']}"
        found = subprocess.run(
            ["ssh", *ssh_base(key), host, f"ls -t {REMOTE_GLOB} 2>/dev/null | head -1"],
            capture_output=True, text=True,
        )
        remote = found.stdout.strip()
        if not remote:
            print(f"skip: {instance['name']} — no resource log found")
            continue
        dest = out / f"{instance['name']}.csv"
        copied = subprocess.run(["scp", *ssh_base(key), f"{host}:{remote}", os.path.relpath(dest)],
                                capture_output=True, text=True)
        if copied.returncode != 0:
            print(f"failed: {instance['name']}: {copied.stderr.strip()}")
            continue
        rows = sum(1 for _ in dest.open(encoding="utf-8")) - 1
        print(f"collected: {instance['name']} {remote} -> {dest} ({rows} samples)")
        collected += 1
    if not collected:
        sys.exit("no resource logs were collected")


if __name__ == "__main__":
    main()
