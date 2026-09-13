from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ControlPlane.schedular import run_etl
from db import db_saver
from db.json_to_rows import transform_records_to_rows


def iter_jsonl_batches(jsonl_path: str, batch_size: int):
    records = []
    degree_count = 0
    with open(jsonl_path, "r", encoding="utf-8") as jf:
        for line in jf:
            text = line.strip()
            if not text:
                continue
            item = json.loads(text)
            degrees = item.get("degrees", [])
            if isinstance(degrees, list):
                degree_count += len(degrees)
            records.append(item)
            if len(records) >= batch_size:
                yield records, degree_count
                records = []
                degree_count = 0
    if records:
        yield records, degree_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run end-to-end ETL: crawl -> direct DB load")
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help="Skip scheduler crawl stage and use latest extracted_records JSONL",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Skip DB load stage after JSONL->rows transform",
    )
    parser.add_argument(
        "--direct-batch-records",
        type=int,
        default=int(os.getenv("ETL_DIRECT_BATCH_RECORDS", "500")),
        help="Batch size (records) for direct JSONL -> DB streaming",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(encoding="utf-8-sig")
    args = parse_args()
    direct_jsonl_path: str | None = None

    if not args.skip_crawl:
        print("[ETL] Stage 1/3: scheduler crawl start", flush=True)
        scheduler_result = asyncio.run(run_etl(persist_summary=True))
        direct_jsonl_path = scheduler_result.get("jsonl_path")
        if not direct_jsonl_path:
            raise RuntimeError("Scheduler did not provide extracted_records JSONL path")

        print(
            "[ETL] Stage 1/3 complete: "
            f"records={scheduler_result.get('total_records', 0)} degrees={scheduler_result.get('total_degrees', 0)}",
            flush=True,
        )
        print(f"[ETL] Stage 1.5/3: direct mode source=jsonl path={direct_jsonl_path}", flush=True)
    else:
        jsonl_candidates = sorted(Path("log").glob("extracted_records_*.jsonl"))
        if not jsonl_candidates:
            raise FileNotFoundError("No extracted_records_*.jsonl found under log/ for direct mode")
        latest_jsonl = str(jsonl_candidates[-1])
        direct_jsonl_path = latest_jsonl
        print(
            f"[ETL] Stage 1 skipped: use latest jsonl={latest_jsonl}",
            flush=True,
        )

    print("[ETL] Stage 2/3: JSON -> rows (direct) start", flush=True)
    batch_size = max(1, int(args.direct_batch_records))

    total_records = 0
    total_degrees = 0
    total_uni = 0
    total_prog = 0
    total_prog_skip = 0
    total_pat = 0
    total_map = 0
    total_map_skip = 0
    chunks = 0

    conn = None
    if not args.skip_load:
        print("[ETL] Stage 3/3: rows -> DB load start (direct streaming)", flush=True)
        conn = db_saver.open_load_session()
        if conn is None:
            raise RuntimeError("db_saver.open_load_session failed")

    try:
        batch_iter = iter_jsonl_batches(direct_jsonl_path, batch_size)

        for batch_records, batch_degrees in batch_iter:
            chunks += 1
            total_records += len(batch_records)
            total_degrees += batch_degrees

            row_bundle = transform_records_to_rows(batch_records)
            row_uni = len(row_bundle.get("universities", []))
            row_prog = len(row_bundle.get("degree_programs", []))
            row_pat = len(row_bundle.get("tuition_patterns", []))
            row_map = len(row_bundle.get("program_tuition_map", []))
            print(
                f"[ETL] chunk={chunks} records={len(batch_records)} rows(u/p/t/m)={row_uni}/{row_prog}/{row_pat}/{row_map}",
                flush=True,
            )

            if not args.skip_load and conn is not None:
                chunk_result = db_saver.load_rows_chunk(conn, row_bundle)
                total_uni += chunk_result["universities"]
                total_prog += chunk_result["degree_programs"]
                total_prog_skip += chunk_result["degree_programs_skipped"]
                total_pat += chunk_result["tuition_patterns"]
                total_map += chunk_result["program_tuition_map"]
                total_map_skip += chunk_result["program_tuition_map_skipped"]

        print(
            f"[ETL] Stage 2/3 complete: chunks={chunks} records={total_records} degrees={total_degrees}",
            flush=True,
        )
        if args.skip_load:
            print("[ETL] Stage 3/3 skipped (--skip-load)", flush=True)
        else:
            print("[ETL] Stage 3/3 complete (direct streaming)", flush=True)
            print(
                "[ETL] loaded totals: "
                f"universities={total_uni} programs={total_prog} program_skip={total_prog_skip} "
                f"patterns={total_pat} maps={total_map} map_skip={total_map_skip}",
                flush=True,
            )
    finally:
        if conn is not None:
            db_saver.close_load_session(conn)


if __name__ == "__main__":
    main()
