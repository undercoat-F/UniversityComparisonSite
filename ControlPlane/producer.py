from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from ControlPlane.dispatcher import enqueue_initial_tasks
from ControlPlane.schedular import filter_targets_by_recent_universities, load_targets, _env_int
from crawler.distributed.record_writer import record_db_enabled

load_dotenv(encoding="utf-8-sig")


def prepare_record_tables() -> None:
    """Worker が抽出結果を書き込む前に、スキーマ作成と ID シーケンス同期を1回だけ行う。

    Worker 側では行わない（書き込み中の他 Worker とシーケンス同期が競合し、主キーが衝突するため）。
    """
    if not record_db_enabled():
        print("[PRODUCER] record DB preparation skipped: RECORD_DB_ENABLED is off", flush=True)
        return
    from db import db_saver

    conn = db_saver.open_load_session()
    if conn is None:
        print("[PRODUCER][WARN] record DB preparation failed; workers may not be able to write records", flush=True)
        return
    db_saver.close_load_session(conn)
    print("[PRODUCER] record DB prepared", flush=True)


async def run_producer() -> None:
    targets = load_targets()
    skip_months = _env_int("ETL_RECENT_SKIP_MONTHS", 6)
    targets = filter_targets_by_recent_universities(targets, skip_months)
    print(f"[PRODUCER] start targets={len(targets)}", flush=True)
    prepare_record_tables()
    sites = await enqueue_initial_tasks(targets)
    print(f"[PRODUCER] finished domains={len(sites)}", flush=True)


if __name__ == "__main__":
    asyncio.run(run_producer())
