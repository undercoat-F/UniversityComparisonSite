from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from ControlPlane.dispatcher import enqueue_initial_tasks
from ControlPlane.schedular import filter_targets_by_recent_universities, load_targets, _env_int

load_dotenv(encoding="utf-8-sig")


async def run_producer() -> None:
    targets = load_targets()
    skip_months = _env_int("ETL_RECENT_SKIP_MONTHS", 6)
    targets = filter_targets_by_recent_universities(targets, skip_months)
    print(f"[PRODUCER] start targets={len(targets)}", flush=True)
    sites = await enqueue_initial_tasks(targets)
    print(f"[PRODUCER] finished domains={len(sites)}", flush=True)


if __name__ == "__main__":
    asyncio.run(run_producer())
