"""シナリオの内容確認ツール

使い方（リポジトリのルートで実行）:
  python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/A_scaling.yaml
  python -m benchmark.mock.inspect_scenario <scenario> --out benchmark/results/A-inspect
  python -m benchmark.mock.inspect_scenario <scenario> --domain d003.bench.internal
  python -m benchmark.mock.inspect_scenario <scenario> --url https://d003.bench.internal/programs/course-0012
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from benchmark.mock.scenario import Scenario, ScenarioError, load_scenario


def _fmt_status(status) -> str:
    return "no response" if status is None else str(status)


def _fmt_mix(counter: Counter, total: int) -> str:
    return " / ".join(f"{name} {count / total:.0%}" for name, count in counter.most_common())


def summarize(sc: Scenario) -> tuple[list[str], list[str]]:
    """(サマリー行, 警告行) を返す。"""
    by_profile = defaultdict(list)
    for domain in sc.domains.values():
        by_profile[domain.profile].append(domain)

    rows = []
    for profile, domains in by_profile.items():
        delays = sorted({d.crawl_delay for d in domains})
        pages = sorted({len(d.pages) - 1 for d in domains})
        mix = Counter(p.response for d in domains for p in d.pages.values() if p.index > 0)
        rt = sc.response_types
        special = []
        robots = {d.settings["robots"] for d in domains}
        sitemap = {d.settings["sitemap"] for d in domains}
        if robots != {"ok"}:
            special.append("robots.txt -> " + ",".join(_fmt_status(rt[r].status) for r in robots))
        if sitemap != {"ok"}:
            special.append("sitemap.xml -> " + ",".join(_fmt_status(rt[s].status) for s in sitemap))
        rows.append((
            profile, len(domains), "/".join(map(str, delays)), "/".join(map(str, pages)),
            _fmt_mix(mix, sum(mix.values()) or 1) + (f"  ({'; '.join(special)})" if special else ""),
        ))

    lines = [f"scenario: {sc.name}  (seed={sc.seed}, source={sc.source})", ""]
    header = ("profile", "domains", "crawl_delay", "pages/domain", "responses")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(4)]
    fmt = lambda r: "  ".join(  # noqa: E731
        [str(r[0]).ljust(widths[0])] + [str(r[i]).rjust(widths[i]) for i in range(1, 4)] + [str(r[4])]
    )
    lines.append(fmt(header))
    lines += [fmt(r) for r in rows]
    lines.append("-" * 72)

    total_pages = sum(len(d.pages) for d in sc.domains.values())
    reachable = {h: sc.reachable_pages(d) for h, d in sc.domains.items()}
    total_reachable = sum(len(r) for r in reachable.values())
    zero_delay = [d for d in sc.domains.values() if d.crawl_delay == 0]
    cap = sum(1 / d.crawl_delay for d in sc.domains.values() if d.crawl_delay > 0)
    lower_bound = max(
        ((len(reachable[d.host]) - 1) * d.crawl_delay for d in sc.domains.values()), default=0
    )
    resp_total = Counter(
        p.response for h, d in sc.domains.items() for p in d.pages.values() if p.path in reachable[h]
    )

    lines.append(f"total domains: {len(sc.domains)}   total pages (incl. root): {total_pages}")
    lines.append(f"reachable pages (root + sitemap + links): {total_reachable}")
    lines.append("reachable pages by response: " + ", ".join(f"{k}={v}" for k, v in resp_total.most_common()))
    if zero_delay:
        lines.append(f"theoretical max throughput: unlimited ({len(zero_delay)} domains have crawl_delay 0)")
    else:
        lines.append(f"theoretical max throughput (sum of 1/crawl_delay): {cap:.1f} URLs/sec")
    lines.append(f"lower bound of total time (crawl_delay only): {lower_bound} sec")

    warnings = []
    checks = sc.checks
    deep = [d.host for d in sc.domains.values() if d.max_tree_depth > checks["crawler_max_depth"]]
    if deep:
        warnings.append(
            f"page tree depth exceeds crawler_max_depth={checks['crawler_max_depth']} "
            f"in {len(deep)} domains (e.g. {deep[0]}); deep pages may not be crawled"
        )
    if not zero_delay and cap < float(checks["min_theoretical_throughput"]):
        warnings.append(
            f"theoretical max throughput {cap:.1f} URLs/sec is below "
            f"min_theoretical_throughput={checks['min_theoretical_throughput']}; "
            "workers may be capped by Crawl-delay (R1.1)"
        )
    for name, rt in sc.response_types.items():
        if rt.status is None and rt.delay_ms < checks["client_timeout_sec"] * 1000:
            warnings.append(
                f"response type '{name}' holds only {rt.delay_ms} ms, shorter than "
                f"client_timeout_sec={checks['client_timeout_sec']}; the client will not time out"
            )
    used_robots = {sc.response_types[d.settings["robots"]] for d in sc.domains.values()}
    if any(rt.status is not None and rt.status >= 500 for rt in used_robots):
        warnings.append("robots.txt returns 5xx in some domains; urllib RobotFileParser then disallows all URLs")
    failing = [n for n, rt in sc.response_types.items() if rt.status is None or rt.status >= 400]
    if any(p.response in failing for d in sc.domains.values() for p in d.pages.values()):
        warnings.append(
            "error/timeout pages: the current crawler retries a failed httpx request with requests "
            "immediately, so these URLs appear twice in the access log"
        )
    return lines, warnings


def write_outputs(sc: Scenario, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rt = sc.response_types
    with (out / "domains.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["host", "profile", "crawl_delay", "pages", "reachable_pages", "max_tree_depth",
                    "robots_status", "sitemap_status", "sitemap_pages"])
        for d in sc.domains.values():
            w.writerow([
                d.host, d.profile, d.crawl_delay, len(d.pages), len(sc.reachable_pages(d)), d.max_tree_depth,
                _fmt_status(rt[d.settings["robots"]].status), _fmt_status(rt[d.settings["sitemap"]].status),
                sum(p.in_sitemap for p in d.pages.values()),
            ])
    with (out / "pages.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "host", "path", "response", "status", "delay_ms", "tree_depth",
                    "in_sitemap", "reachable", "links"])
        for d in sc.domains.values():
            reachable = sc.reachable_pages(d)
            for p in d.pages.values():
                w.writerow([
                    sc.url(d.host, p.path), d.host, p.path, p.response, _fmt_status(rt[p.response].status),
                    p.delay_ms, p.tree_depth, int(p.in_sitemap), int(p.path in reachable), len(p.links),
                ])
    (out / "seed_urls.txt").write_text("\n".join(sc.seed_urls()) + "\n", encoding="utf-8")
    (out / "hosts.txt").write_text("\n".join(sc.domains) + "\n", encoding="utf-8")
    (out / "scenario.resolved.json").write_text(
        json.dumps(
            {
                "scenario": sc.name, "seed": sc.seed, "scheme": sc.scheme, "checks": sc.checks,
                "response_types": {k: vars(v) for k, v in sc.response_types.items()},
                "domains": {h: {"profile": d.profile, **d.settings} for h, d in sc.domains.items()},
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    if sc.source:
        shutil.copyfile(sc.source, out / sc.source.name)


def show_domain(sc: Scenario, host: str) -> None:
    d = sc.domains.get(host)
    if d is None:
        sys.exit(f"unknown domain: {host}")
    print(f"{host}  profile={d.profile}  crawl_delay={d.crawl_delay}")
    print(json.dumps(d.settings, ensure_ascii=False))
    reachable = sc.reachable_pages(d)
    print(f"{'path':40} {'response':10} {'status':>7} {'delay':>6} depth sitemap reachable")
    for p in d.pages.values():
        print(f"{p.path:40} {p.response:10} {_fmt_status(sc.response_types[p.response].status):>7} "
              f"{p.delay_ms:>6} {p.tree_depth:>5} {'yes' if p.in_sitemap else '-':>7} "
              f"{'yes' if p.path in reachable else 'NO':>9}")


def show_url(sc: Scenario, url: str) -> None:
    parsed = urlparse(url)
    resolved = sc.resolve(parsed.netloc, parsed.path or "/")
    if resolved is None:
        print(f"{url}\n-> 404 (not defined in scenario)")
        return
    rt, delay, content_type, body = resolved
    print(f"{url}")
    print(f"response type: {rt.name}  status: {_fmt_status(rt.status)}  delay: {delay} ms  content-type: {content_type}")
    if rt.status is None:
        print("(no response is sent; the connection is held until the client disconnects)")
        return
    print("-" * 72)
    print(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a benchmark mock scenario")
    parser.add_argument("scenario", help="scenario YAML path")
    parser.add_argument("--out", help="write domains.csv / pages.csv / seed_urls.txt etc. to this directory")
    parser.add_argument("--domain", help="show all pages of this domain")
    parser.add_argument("--url", help="show the response returned for this URL")
    args = parser.parse_args()

    try:
        sc = load_scenario(args.scenario)
    except ScenarioError as exc:
        sys.exit(f"scenario error: {exc}")

    if args.url:
        show_url(sc, args.url)
        return
    if args.domain:
        show_domain(sc, args.domain)
        return

    lines, warnings = summarize(sc)
    print("\n".join(lines))
    for w in warnings:
        print(f"WARNING: {w}")
    if args.out:
        write_outputs(sc, Path(args.out))
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
