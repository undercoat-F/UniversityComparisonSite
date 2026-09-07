from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ControlPlane.queue_log import QueueLogStore


def run_measurement(
    events: int,
    batch_size: int,
    flush_every: int,
    failures_only: bool,
    pg_dsn: str,
) -> None:
    root = ROOT
    schema_path = root / "ControlPlane" / "queue_log_schema_pg.sql"

    store = QueueLogStore(
        db_path="",
        schema_path=str(schema_path),
        pg_dsn=pg_dsn,
        batch_size=batch_size,
        failures_only=failures_only,
    )
    store.init_db()
    run_id = store.create_run(root_seed_count=1, notes="flush benchmark")

    enqueue_times_ms: list[float] = []
    flush_times_ms: list[float] = []
    flush_calls = 0

    for i in range(events):
        started = time.perf_counter()
        store.add_attempt(
            run_id=run_id,
            url=f"https://example.com/item/{i}",
            fetch_method="httpx",
            ok=False,
            status_code=403,
            error_type="HTTPStatusError",
            error_message="403 Forbidden",
            final_url=f"https://example.com/item/{i}",
            response_bytes=None,
            used_fallback=False,
            connection_log=[{"client": "httpx", "ok": False, "status_code": 403}],
        )
        enqueue_times_ms.append((time.perf_counter() - started) * 1000.0)

        if flush_every > 0 and (i + 1) % flush_every == 0:
            flush_started = time.perf_counter()
            store.flush()
            flush_times_ms.append((time.perf_counter() - flush_started) * 1000.0)
            flush_calls += 1

    flush_started = time.perf_counter()
    store.flush()
    flush_times_ms.append((time.perf_counter() - flush_started) * 1000.0)
    flush_calls += 1

    store.finish_run(run_id=run_id, status="completed", notes="flush benchmark done")
    store.close()

    def stat_line(name: str, values: list[float]) -> str:
        if not values:
            return f"{name}: n=0"
        return (
            f"{name}: n={len(values)} "
            f"avg={statistics.mean(values):.4f}ms "
            f"p95={statistics.quantiles(values, n=20)[18]:.4f}ms "
            f"max={max(values):.4f}ms"
        )

    print("=== Queue Log Flush Metrics ===")
    print(f"events={events} batch_size={batch_size} flush_every={flush_every} failures_only={failures_only}")
    print(f"schema={schema_path}")
    print(stat_line("enqueue", enqueue_times_ms))
    print(stat_line("flush", flush_times_ms))
    print(f"flush_calls={flush_calls}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure enqueue/flush cost for QueueLogStore")
    parser.add_argument("--events", type=int, default=1000, help="Number of log events to enqueue")
    parser.add_argument("--batch-size", type=int, default=200, help="QueueLogStore batch_size")
    parser.add_argument(
        "--flush-every",
        type=int,
        default=200,
        help="Call flush() every N events (0 = only final flush)",
    )
    parser.add_argument(
        "--all-events",
        action="store_true",
        help="Disable failures_only mode and store all event kinds",
    )
    parser.add_argument(
        "--pg-dsn",
        type=str,
        default="",
        help="PostgreSQL DSN (if omitted, use PARENT_DB_OWNER_CONNECTION)",
    )
    args = parser.parse_args()

    pg_dsn = args.pg_dsn.strip() or os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
    if not pg_dsn:
        raise SystemExit("PARENT_DB_OWNER_CONNECTION or --pg-dsn is required")

    run_measurement(
        events=args.events,
        batch_size=args.batch_size,
        flush_every=args.flush_every,
        failures_only=not args.all_events,
        pg_dsn=pg_dsn,
    )
