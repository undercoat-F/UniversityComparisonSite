from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from crawler.crawlworker import seed_sitemap_candidates
from ETL.dispatcher import build_site_states
from ETL.schedular import load_targets


@dataclass
class ProbeResult:
    domain: str
    elapsed_sec: float
    start_url_count: int
    sitemap_url_count: int
    candidate_count: int
    error: str


async def _probe_one(site, sem: asyncio.Semaphore) -> ProbeResult:
    started = time.perf_counter()
    error = ""

    async with sem:
        try:
            await seed_sitemap_candidates(site)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

    elapsed = time.perf_counter() - started
    return ProbeResult(
        domain=site.domain,
        elapsed_sec=elapsed,
        start_url_count=len(site.start_urls),
        sitemap_url_count=len(site.sitemap_urls),
        candidate_count=site.sitemap_candidate_count,
        error=error,
    )


async def run_probe(limit: int | None, concurrency: int, progress_every: int) -> tuple[list[ProbeResult], float]:
    targets = load_targets()
    sites = build_site_states(targets)

    if limit is not None and limit > 0:
        sites = sites[:limit]

    print(f"[PROBE] targets={len(targets)} unique_sites={len(sites)} concurrency={concurrency}", flush=True)

    sem = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    tasks = [asyncio.create_task(_probe_one(site, sem)) for site in sites]

    results: list[ProbeResult] = []
    completed = 0
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        completed += 1

        if completed % max(1, progress_every) == 0 or completed == len(tasks):
            elapsed = time.perf_counter() - started
            errors = sum(1 for r in results if r.error)
            print(
                f"[PROBE] done={completed}/{len(tasks)} elapsed={elapsed:.1f}s errors={errors}",
                flush=True,
            )

    total_elapsed = time.perf_counter() - started
    return results, total_elapsed


def write_csv(results: list[ProbeResult], out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "domain",
                "elapsed_sec",
                "start_url_count",
                "sitemap_url_count",
                "candidate_count",
                "error",
            ]
        )
        for row in results:
            writer.writerow(
                [
                    row.domain,
                    f"{row.elapsed_sec:.3f}",
                    row.start_url_count,
                    row.sitemap_url_count,
                    row.candidate_count,
                    row.error,
                ]
            )


def print_summary(results: list[ProbeResult], total_elapsed: float, top_n: int) -> None:
    if not results:
        print("[PROBE] no results", flush=True)
        return

    errors = [r for r in results if r.error]
    avg = sum(r.elapsed_sec for r in results) / len(results)
    sorted_rows = sorted(results, key=lambda r: r.elapsed_sec, reverse=True)

    print(
        f"[PROBE] finished sites={len(results)} total_elapsed={total_elapsed:.1f}s avg_per_site={avg:.2f}s errors={len(errors)}",
        flush=True,
    )

    print(f"[PROBE] top {min(top_n, len(sorted_rows))} slow domains", flush=True)
    for row in sorted_rows[:top_n]:
        print(
            f"  - {row.domain} elapsed={row.elapsed_sec:.2f}s sitemap_urls={row.sitemap_url_count} candidates={row.candidate_count} error={row.error or '-'}",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure sitemap seeding stage duration per domain")
    parser.add_argument(
        "--limit",
        type=int,
        default=120,
        help="Number of unique domains to probe (0 or negative means all)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=12,
        help="Max concurrent domain probes",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print progress every N completed domains",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Show top N slow domains",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="CSV output path (default: log/seed_sitemap_probe_<timestamp>.csv)",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(encoding="utf-8-sig")
    args = parse_args()

    limit = None if args.limit <= 0 else args.limit
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.output.strip() or os.path.join("log", f"seed_sitemap_probe_{now}.csv")

    results, total_elapsed = asyncio.run(
        run_probe(
            limit=limit,
            concurrency=max(1, args.concurrency),
            progress_every=max(1, args.progress_every),
        )
    )
    write_csv(results, out_path)
    print_summary(results, total_elapsed, top_n=max(1, args.top))
    print(f"[PROBE] csv={out_path}", flush=True)


if __name__ == "__main__":
    main()
