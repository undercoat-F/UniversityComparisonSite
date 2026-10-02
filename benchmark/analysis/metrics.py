"""Mock Server のアクセスログから性能指標を算出する（benchmark_concept_revisions.md R2.4, R4）。

入力（1回の計測 = 1 run ディレクトリ。server.py が出力する）:
  access.jsonl   アクセスログ
  domains.csv    ドメインごとの設定（crawl_delay, robots_status など）
  pages.csv      ページごとの設定（reachable, status など）
  workers.csv    任意。client_ip,worker_id の対応表（R2.3）
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_TOLERANCE_SEC = 0.05  # R4.4
SPECIAL_PATHS = {"/robots.txt": "robots", "/sitemap.xml": "sitemap"}
RUN_SUFFIX = re.compile(r"-run\d+$")


@dataclass
class Request:
    recv_ts: float
    send_ts: float
    host: str
    path: str
    client_ip: str
    status: Optional[int]
    response_type: str
    injected_delay_ms: int
    outcome: str

    @property
    def kind(self) -> str:
        return SPECIAL_PATHS.get(self.path, "page")

    @property
    def ok(self) -> bool:
        return self.outcome == "sent" and self.status is not None and 200 <= self.status < 300


@dataclass
class DomainInfo:
    host: str
    profile: str
    crawl_delay: int
    robots_status: str


@dataclass
class RunData:
    label: str
    path: Path
    requests: list[Request]
    domains: dict[str, DomainInfo]
    expected_urls: set[tuple[str, str]]              # reachable なページ
    expected_status: dict[tuple[str, str], str]      # ページごとのシナリオ上のステータス
    workers: dict[str, str] = field(default_factory=dict)  # client_ip -> worker_id

    @property
    def config(self) -> str:
        """-runN を除いたラベル。同一構成の繰り返し計測をまとめるのに使う。"""
        return RUN_SUFFIX.sub("", self.label)


def load_run(run_dir: str | Path) -> RunData:
    run_dir = Path(run_dir)
    requests = []
    label = run_dir.name
    with (run_dir / "access.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            label = r.get("run_label") or label
            requests.append(Request(
                recv_ts=float(r["recv_ts"]),
                send_ts=float(r["send_ts"] if r.get("send_ts") is not None else r["recv_ts"]),
                host=r["host"],
                path=r["path"],
                client_ip=r.get("client_ip", ""),
                status=r.get("status"),
                response_type=r.get("response_type") or "",
                injected_delay_ms=int(r.get("injected_delay_ms") or 0),
                outcome=r.get("outcome", "sent"),
            ))
    requests.sort(key=lambda r: r.recv_ts)

    domains = {}
    with (run_dir / "domains.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            domains[row["host"]] = DomainInfo(
                host=row["host"], profile=row["profile"],
                crawl_delay=int(row["crawl_delay"]), robots_status=row["robots_status"],
            )

    expected_urls, expected_status = set(), {}
    with (run_dir / "pages.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["host"], row["path"])
            expected_status[key] = row["status"]
            if row["reachable"] == "1":
                expected_urls.add(key)

    workers = {}
    workers_csv = run_dir / "workers.csv"
    if workers_csv.exists():
        with workers_csv.open(encoding="utf-8", newline="") as f:
            workers = {row["client_ip"]: row["worker_id"] for row in csv.DictReader(f)}

    return RunData(label, run_dir, requests, domains, expected_urls, expected_status, workers)


def percentile(values: list[float], q: float) -> Optional[float]:
    """線形補間の百分位数（numpy の既定と同じ）。"""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _is_scenario_failure(expected_status: str) -> bool:
    return not expected_status.isdigit() or int(expected_status) >= 400


def compute_run_metrics(run: RunData) -> dict[str, Any]:
    reqs = [r for r in run.requests if r.host in run.domains]
    undefined = [r for r in run.requests if r.host not in run.domains or r.response_type == "undefined"]
    pages = [r for r in reqs if r.kind == "page"]

    if not reqs:
        return {"label": run.label, "config": run.config, "requests": 0}

    start = min(r.recv_ts for r in reqs)
    end = max(r.send_ts for r in reqs)
    total_time = end - start

    # URL単位：同一URLへの再取得（httpx→requests のフォールバック, R4.2）は1件とし、最後の応答で成否を判定する
    by_url: dict[tuple[str, str], list[Request]] = defaultdict(list)
    for r in pages:
        by_url[(r.host, r.path)].append(r)
    failed_urls = {k for k, rs in by_url.items() if not rs[-1].ok}
    unexpected_failed = sorted(
        k for k in failed_urls if not _is_scenario_failure(run.expected_status.get(k, "404"))
    )
    fetched = set(by_url)
    missing = run.expected_urls - fetched

    latencies = [(r.send_ts - r.recv_ts) * 1000 for r in pages if r.outcome == "sent"]
    overheads = [(r.send_ts - r.recv_ts) * 1000 - r.injected_delay_ms for r in pages if r.outcome == "sent"]

    per_worker = Counter(run.workers.get(r.client_ip, r.client_ip) for r in pages)

    return {
        "label": run.label,
        "config": run.config,
        "start_ts": start,
        "end_ts": end,
        "total_time_sec": total_time,
        "requests": len(reqs),
        "page_requests": len(pages),
        "robots_requests": sum(r.kind == "robots" for r in reqs),
        "sitemap_requests": sum(r.kind == "sitemap" for r in reqs),
        "undefined_requests": len(undefined),
        "urls": len(fetched),
        "expected_urls": len(run.expected_urls),
        "coverage": len(fetched & run.expected_urls) / len(run.expected_urls) if run.expected_urls else None,
        "missing_urls": len(missing),
        "retried_urls": sum(len(rs) > 1 for rs in by_url.values()),
        "urls_per_sec": len(fetched) / total_time if total_time > 0 else None,
        "requests_per_sec": len(pages) / total_time if total_time > 0 else None,
        "failed_urls": len(failed_urls),
        "url_error_rate": len(failed_urls) / len(fetched) if fetched else None,
        "request_error_rate": sum(not r.ok for r in pages) / len(pages) if pages else None,
        "unexpected_failed_urls": len(unexpected_failed),
        "unexpected_failed_examples": [f"{h}{p}" for h, p in unexpected_failed[:10]],
        "server_latency_ms": {
            "p50": percentile(latencies, 50), "p95": percentile(latencies, 95), "max": max(latencies, default=None),
        },
        "mock_overhead_ms": {"p50": percentile(overheads, 50), "p95": percentile(overheads, 95)},
        "workers": len(per_worker),
        "page_requests_by_worker": dict(per_worker.most_common()),
    }


def compute_crawl_delay(run: RunData, tolerance_sec: float = DEFAULT_TOLERANCE_SEC) -> list[dict[str, Any]]:
    """ドメインごとの Crawl-delay 遵守状況。ページへのリクエストの受信間隔で判定する。

    - 同一URLへの連続リクエスト（フォールバック再取得, R4.2）は1回の取得とみなし、retry_pairs に数える
    - robots.txt が 200 でないドメインはクローラーが Crawl-delay を知り得ないため判定対象外（R4.3）
    """
    by_host: dict[str, list[Request]] = defaultdict(list)
    for r in run.requests:
        if r.host in run.domains and r.kind == "page":
            by_host[r.host].append(r)

    results = []
    for host, info in run.domains.items():
        rs = by_host.get(host, [])
        applicable = info.robots_status == "200" and info.crawl_delay > 0
        # 再取得は直前の取得とまとめ、間隔は各URLの最初のリクエストどうしで測る
        # （クローラーは最初のリクエストの開始時に最終アクセス時刻を記録するため）
        intervals, retry_intervals = [], []
        for prev, cur in zip(rs, rs[1:]):
            if cur.path == prev.path:
                retry_intervals.append(cur.recv_ts - prev.recv_ts)
        attempts = [r for i, r in enumerate(rs) if i == 0 or r.path != rs[i - 1].path]
        intervals = [b.recv_ts - a.recv_ts for a, b in zip(attempts, attempts[1:])]
        violations = (
            sum(g < info.crawl_delay - tolerance_sec for g in intervals) if applicable else None
        )
        results.append({
            "host": host,
            "profile": info.profile,
            "required_delay": info.crawl_delay if applicable else None,
            "applicable": applicable,
            "page_requests": len(rs),
            "pairs": len(intervals),
            "min_interval": min(intervals, default=None),
            "p50_interval": percentile(intervals, 50),
            "violations": violations,
            "retry_pairs": len(retry_intervals),
            "min_retry_interval": min(retry_intervals, default=None),
            "clients": len({r.client_ip for r in rs}),
        })
    return results


def summarize_crawl_delay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [r for r in rows if r["applicable"] and r["pairs"]]
    return {
        "domains_checked": len(applicable),
        "domains_excluded": sum(not r["applicable"] for r in rows),
        "violations": sum(r["violations"] for r in applicable),
        "domains_with_violations": sum(r["violations"] > 0 for r in applicable),
        "min_margin_sec": min((r["min_interval"] - r["required_delay"] for r in applicable), default=None),
        "retry_pairs": sum(r["retry_pairs"] for r in rows),
    }
