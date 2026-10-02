"""計測結果の集計レポートを作成する。

使い方（リポジトリのルートで実行）:
  # 1回分の計測を集計（run ディレクトリに summary.json / crawl_delay.csv / report.md を出力）
  python -m benchmark.analysis.report run benchmark/results/A-distributed-x2-run1

  # 複数の計測を比較（benchmark_concept.md 11.1 / 11.2 の表）。-runN を除いたラベルが同じ計測は中央値でまとめる
  python -m benchmark.analysis.report compare benchmark/results/A-* --baseline A-distributed-x1 --out benchmark/results/A-compare.md
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from benchmark.analysis.metrics import (
    DEFAULT_TOLERANCE_SEC,
    RunData,
    compute_crawl_delay,
    compute_run_metrics,
    load_run,
    summarize_crawl_delay,
)

LATENCY_NOTE = (
    "p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。"
    "クライアント側から見た応答時間ではない（R2.5）。"
)


def _f(value: Optional[float], fmt: str = "{:.2f}", none: str = "-") -> str:
    return none if value is None else fmt.format(value)


def _pct(value: Optional[float]) -> str:
    return _f(None if value is None else value * 100, "{:.1f}%")


def _table(header: list[str], rows: list[list[str]], align: Optional[list[str]] = None) -> str:
    align = align or ["l"] + ["r"] * (len(header) - 1)
    sep = ["---:" if a == "r" else "---" for a in align]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def analyze(run: RunData, tolerance: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metrics = compute_run_metrics(run)
    delay_rows = compute_crawl_delay(run, tolerance)
    metrics["crawl_delay"] = summarize_crawl_delay(delay_rows)
    metrics["crawl_delay_tolerance_sec"] = tolerance
    resolved = run.path / "scenario.resolved.json"
    if resolved.exists():
        metrics["scenario"] = json.loads(resolved.read_text(encoding="utf-8")).get("scenario")
    return metrics, delay_rows


def warnings_for(m: dict[str, Any]) -> list[str]:
    w = []
    if m.get("requests", 0) == 0:
        return ["no requests to scenario domains in access.jsonl"]
    if m["coverage"] is not None and m["coverage"] < 1.0:
        w.append(f"coverage {m['coverage']:.1%}: {m['missing_urls']} reachable URLs were not fetched")
    if m["unexpected_failed_urls"]:
        w.append(f"{m['unexpected_failed_urls']} URLs failed although the scenario returns 200 "
                 f"(e.g. {', '.join(m['unexpected_failed_examples'][:3])})")
    if m["undefined_requests"]:
        w.append(f"{m['undefined_requests']} requests to hosts/paths not defined in the scenario")
    if m["crawl_delay"]["violations"]:
        w.append(f"{m['crawl_delay']['violations']} Crawl-delay violations in "
                 f"{m['crawl_delay']['domains_with_violations']} domains")
    overhead = m["mock_overhead_ms"]["p95"]
    if overhead is not None and overhead > 50:
        w.append(f"mock overhead p95 {overhead:.0f} ms: the mock server may be a bottleneck")
    return w


def render_run(m: dict[str, Any], delay_rows: list[dict[str, Any]]) -> str:
    if m.get("requests", 0) == 0:
        return f"# {m['label']}\n\nno requests\n"
    cd = m["crawl_delay"]
    lines = [
        f"# {m['label']}",
        "",
        f"- scenario: {m.get('scenario', '-')}",
        f"- total time: {m['total_time_sec']:.1f} sec",
        f"- URLs: {m['urls']} / expected {m['expected_urls']} (coverage {_pct(m['coverage'])})",
        f"- throughput: {_f(m['urls_per_sec'])} URLs/sec ({_f(m['requests_per_sec'])} requests/sec)",
        f"- requests: pages {m['page_requests']}, robots {m['robots_requests']}, sitemap {m['sitemap_requests']}, "
        f"undefined {m['undefined_requests']}",
        f"- error rate: URL {_pct(m['url_error_rate'])} ({m['failed_urls']} URLs), "
        f"request {_pct(m['request_error_rate'])}, retried URLs {m['retried_urls']}",
        f"- unexpected failures: {m['unexpected_failed_urls']}",
        f"- server latency: p50 {_f(m['server_latency_ms']['p50'], '{:.0f}')} ms, "
        f"p95 {_f(m['server_latency_ms']['p95'], '{:.0f}')} ms, max {_f(m['server_latency_ms']['max'], '{:.0f}')} ms",
        f"- mock overhead: p50 {_f(m['mock_overhead_ms']['p50'], '{:.1f}')} ms, "
        f"p95 {_f(m['mock_overhead_ms']['p95'], '{:.1f}')} ms",
        f"- Crawl-delay: {cd['violations']} violations in {cd['domains_checked']} domains "
        f"(tolerance {m['crawl_delay_tolerance_sec'] * 1000:.0f} ms, min margin {_f(cd['min_margin_sec'], '{:+.3f}')} sec, "
        f"excluded {cd['domains_excluded']} domains, retry pairs {cd['retry_pairs']})",
        "",
        "## Workers",
        "",
        _table(["worker", "page requests", "share"], [
            [w, str(n), _pct(n / m["page_requests"])] for w, n in m["page_requests_by_worker"].items()
        ]),
        "",
        "## Crawl-delay by required delay",
        "",
        _crawl_delay_table({m["label"]: delay_rows}),
    ]
    violating = [r for r in delay_rows if r["violations"]]
    if violating:
        lines += ["", "## Domains with violations", "", _table(
            ["host", "required", "min interval", "violations", "pairs", "clients"],
            [[r["host"], str(r["required_delay"]), _f(r["min_interval"], "{:.3f}"), str(r["violations"]),
              str(r["pairs"]), str(r["clients"])] for r in violating],
        )]
    warns = warnings_for(m)
    if warns:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warns]
    lines += ["", f"※ {LATENCY_NOTE}", ""]
    return "\n".join(lines)


def _crawl_delay_table(rows_by_config: dict[str, list[dict[str, Any]]]) -> str:
    """構成 × 必要 Crawl-delay ごとにまとめた表（benchmark_concept.md 11.2）。"""
    out = []
    for config, rows in rows_by_config.items():
        groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[r["required_delay"]].append(r)
        for delay in sorted(groups, key=lambda d: (d is None, d)):
            g = [r for r in groups[delay] if r["pairs"]]
            hosts = str(len({r["host"] for r in groups[delay]}))
            if delay is None:
                out.append([config, "excluded (robots.txt not 200)", hosts, "-", "-", "-"])
                continue
            out.append([
                config, str(delay), hosts,
                _f(min((r["min_interval"] for r in g), default=None), "{:.3f}"),
                str(sum(r["violations"] for r in g)),
                str(sum(r["retry_pairs"] for r in groups[delay])),
            ])
    return _table(["Architecture", "Required Delay", "Domains", "Minimum Observed", "Violations", "Retry pairs"],
                  out, ["l", "r", "r", "r", "r", "r"])


def cmd_run(args: argparse.Namespace) -> None:
    for run_dir in args.run_dirs:
        run = load_run(run_dir)
        metrics, delay_rows = analyze(run, args.tolerance)
        if not args.out:
            out = Path(run_dir)
        else:
            out = Path(args.out) / run.label if len(args.run_dirs) > 1 else Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        with (out / "crawl_delay.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(delay_rows[0]) if delay_rows else ["host"])
            writer.writeheader()
            writer.writerows(delay_rows)
        report = render_run(metrics, delay_rows)
        (out / "report.md").write_text(report, encoding="utf-8")
        print(report)


def _median(values: list[Optional[float]]) -> Optional[float]:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def cmd_compare(args: argparse.Namespace) -> None:
    configs: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = defaultdict(list)
    for run_dir in args.run_dirs:
        run = load_run(run_dir)
        configs[run.config].append(analyze(run, args.tolerance))

    baseline = args.baseline or next(iter(configs))
    if baseline not in configs:
        sys.exit(f"baseline '{baseline}' not found; configs: {', '.join(configs)}")

    agg = {}
    for config, runs in configs.items():
        ms = [m for m, _ in runs if m.get("requests")]
        agg[config] = {
            "runs": len(runs),
            "workers": _median([m["workers"] for m in ms]),
            "urls": _median([m["urls"] for m in ms]),
            "coverage": _median([m["coverage"] for m in ms]),
            "total_time": _median([m["total_time_sec"] for m in ms]),
            "urls_per_sec": _median([m["urls_per_sec"] for m in ms]),
            "p50": _median([m["server_latency_ms"]["p50"] for m in ms]),
            "p95": _median([m["server_latency_ms"]["p95"] for m in ms]),
            "url_error_rate": _median([m["url_error_rate"] for m in ms]),
            "unexpected": sum(m["unexpected_failed_urls"] for m in ms),
            "violations": sum(m["crawl_delay"]["violations"] for m in ms),
            "scenarios": {m.get("scenario") for m in ms},
            "expected_urls": {m["expected_urls"] for m in ms},
        }

    base = agg[baseline]
    rows = []
    for config, a in agg.items():
        ratio = (a["urls_per_sec"] / base["urls_per_sec"]
                 if a["urls_per_sec"] and base["urls_per_sec"] else None)
        worker_ratio = a["workers"] / base["workers"] if a["workers"] and base["workers"] else None
        rows.append([
            config, str(a["runs"]), _f(a["workers"], "{:.0f}"), _f(a["urls"], "{:.0f}"), _pct(a["coverage"]),
            _f(a["total_time"], "{:.1f}"), _f(a["urls_per_sec"]), _f(ratio, "{:.2f}x"),
            _f(ratio / worker_ratio if ratio and worker_ratio else None, "{:.0%}"),
            _f(a["p50"], "{:.0f}"), _f(a["p95"], "{:.0f}"), _pct(a["url_error_rate"]),
            str(a["unexpected"]), str(a["violations"]), "-", "-",
        ])

    warnings = []
    scenarios = set().union(*(a["scenarios"] for a in agg.values()))
    expected = set().union(*(a["expected_urls"] for a in agg.values()))
    if len(scenarios) > 1 or len(expected) > 1:
        warnings.append(f"runs use different scenarios ({', '.join(map(str, scenarios))}); results are not comparable")
    for config, runs in configs.items():
        for m, _ in runs:
            warnings += [f"{m['label']}: {w}" for w in warnings_for(m)]

    lines = [
        "# Benchmark comparison",
        "",
        f"baseline: {baseline} / 同一構成の複数回計測は中央値（Violations と Unexpected failures は合計）",
        "",
        "## Performance",
        "",
        _table(
            ["Architecture", "Runs", "Workers", "URLs", "Coverage", "Total Time (s)", "URLs/sec", "vs baseline",
             "Scale eff.", "p50 (ms)", "p95 (ms)", "Error Rate", "Unexpected failures", "Crawl-delay violations",
             "CPU", "Memory"],
            rows,
        ),
        "",
        "- vs baseline: URLs/sec の baseline 比。Scale eff.: vs baseline ÷ Worker数の比（100% で線形スケール）",
        f"- {LATENCY_NOTE}",
        "- CPU / Memory は CloudWatch 等から別途記入する",
        "",
        "## Crawl-delay",
        "",
        _crawl_delay_table({
            config: [row for _, delay_rows in runs for row in delay_rows] for config, runs in configs.items()
        }),
    ]
    if warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
    report = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark result analysis")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="analyze run directories one by one")
    p_run.add_argument("run_dirs", nargs="+")
    p_run.add_argument("--out", help="output directory (default: each run directory)")
    p_run.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_SEC, help="Crawl-delay tolerance (sec)")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare run directories")
    p_cmp.add_argument("run_dirs", nargs="+")
    p_cmp.add_argument("--baseline", help="config label used as the baseline (default: first)")
    p_cmp.add_argument("--out", help="write the markdown report to this file")
    p_cmp.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_SEC, help="Crawl-delay tolerance (sec)")
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
