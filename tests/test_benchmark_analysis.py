#!/usr/bin/env python
# -*- coding: utf-8 -*-

import csv
import json
import tempfile
import unittest
from pathlib import Path

from benchmark.analysis.metrics import (
    compute_crawl_delay,
    compute_run_metrics,
    load_run,
    percentile,
    summarize_crawl_delay,
)

T0 = 1_800_000_000.0


def _req(t, host, path, status=200, ip="10.0.0.1", rtype="fast", outcome="sent", duration=0.01):
    return {
        "recv_ts": T0 + t, "send_ts": T0 + t + duration, "host": host, "path": path,
        "client_ip": ip, "status": status, "response_type": rtype, "injected_delay_ms": 10,
        "outcome": outcome, "run_label": "A-distributed-x2-run1",
    }


def _write_run(root: Path, requests, domains, pages, workers=None) -> Path:
    run_dir = root / "A-distributed-x2-run1"
    run_dir.mkdir()
    with (run_dir / "access.jsonl").open("w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r) for r in requests) + "\n")
    with (run_dir / "domains.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["host", "profile", "crawl_delay", "robots_status"])
        w.writerows(domains)
    with (run_dir / "pages.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["host", "path", "status", "reachable"])
        w.writerows(pages)
    if workers:
        with (run_dir / "workers.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["client_ip", "worker_id"])
            w.writerows(workers)
    return run_dir


class TestBenchmarkAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        a, b, c = "a.bench.internal", "b.bench.internal", "c.bench.internal"
        requests = [
            _req(0.0, a, "/robots.txt", rtype="ok"),
            _req(0.1, a, "/"),
            _req(1.1, a, "/p1", ip="10.0.0.2"),
            _req(2.1, a, "/p2", status=500, rtype="error_500"),
            _req(2.15, a, "/p2", status=500, rtype="error_500"),  # フォールバック再取得
            _req(3.08, a, "/p3", ip="10.0.0.2"),                   # 元の /p2 から0.98秒：許容誤差内
            _req(4.1, a, "/p4", status=None, rtype="fast", outcome="client_disconnected"),  # 想定外の失敗
            _req(0.2, b, "/"),
            _req(1.0, b, "/p1"),                                   # 0.8秒 < 2秒 → 違反
            _req(0.3, c, "/"),
            _req(0.35, c, "/p1"),                                  # robots 404 のため判定対象外
            _req(0.4, "unknown.bench.internal", "/", status=404, rtype="undefined"),
        ]
        domains = [(a, "failure", 1, "200"), (b, "normal", 2, "200"), (c, "robots_error", 1, "404")]
        pages = [
            (a, "/", "200", "1"), (a, "/p1", "200", "1"), (a, "/p2", "500", "1"), (a, "/p3", "200", "1"),
            (a, "/p4", "200", "1"), (a, "/p5", "200", "1"),
            (b, "/", "200", "1"), (b, "/p1", "200", "1"),
            (c, "/", "200", "1"), (c, "/p1", "200", "1"),
        ]
        run_dir = _write_run(self.root, requests, domains, pages, workers=[("10.0.0.1", "w1"), ("10.0.0.2", "w2")])
        self.run = load_run(run_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertAlmostEqual(percentile(list(range(1, 101)), 95), 95.05)
        self.assertIsNone(percentile([], 50))

    def test_run_metrics_count_urls_retries_and_failures(self):
        m = compute_run_metrics(self.run)

        self.assertEqual(m["config"], "A-distributed-x2")
        self.assertEqual(m["urls"], 9)
        self.assertEqual(m["page_requests"], 10)
        self.assertEqual(m["expected_urls"], 10)
        self.assertEqual(m["missing_urls"], 1)
        self.assertEqual(m["retried_urls"], 1)
        self.assertEqual(m["failed_urls"], 2)
        self.assertEqual(m["unexpected_failed_urls"], 1)
        self.assertEqual(m["unexpected_failed_examples"], ["a.bench.internal/p4"])
        self.assertEqual(m["undefined_requests"], 1)
        self.assertAlmostEqual(m["total_time_sec"], 4.11, places=5)
        self.assertAlmostEqual(m["urls_per_sec"], 9 / 4.11, places=5)
        self.assertEqual(m["workers"], 2)
        self.assertEqual(m["page_requests_by_worker"], {"w1": 8, "w2": 2})

    def test_crawl_delay_separates_retries_and_excludes_robots_failures(self):
        rows = {r["host"]: r for r in compute_crawl_delay(self.run, tolerance_sec=0.05)}

        a = rows["a.bench.internal"]
        self.assertEqual(a["violations"], 0)
        self.assertEqual(a["retry_pairs"], 1)
        self.assertEqual(a["pairs"], 4)
        self.assertAlmostEqual(a["min_interval"], 0.98, places=5)  # /p2（元のリクエスト）→ /p3
        self.assertAlmostEqual(a["min_retry_interval"], 0.05, places=5)
        self.assertEqual(a["clients"], 2)

        self.assertEqual(rows["b.bench.internal"]["violations"], 1)
        self.assertFalse(rows["c.bench.internal"]["applicable"])
        self.assertIsNone(rows["c.bench.internal"]["violations"])

        summary = summarize_crawl_delay(list(rows.values()))
        self.assertEqual(summary["violations"], 1)
        self.assertEqual(summary["domains_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
