from __future__ import annotations

import asyncio
import os

import httpx
from dotenv import load_dotenv

from crawler.crawlworker import DEFAULT_HEADERS, ensure_robots, run_url_task
from dataclass.dataclass import SiteState, URLTask
from ETL.dedup_store import get_dedup_store
from ETL.sqs_queue import TaskQueue, get_task_queue
from ETL.worker_site_cache import DomainSiteCache

load_dotenv(encoding="utf-8-sig")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _get_dedup_store_or_none():
    if not os.getenv("REDIS_URL", "").strip():
        print("[WORKER] REDIS_URL is not set; running without dedup store", flush=True)
        return None
    try:
        return get_dedup_store()
    except Exception as exc:  # noqa: BLE001
        print(f"[WORKER][WARN] could not initialize dedup store: {type(exc).__name__}: {exc}", flush=True)
        return None


async def _forward_discovered_tasks(site: SiteState, task_queue: TaskQueue) -> None:
    """run_url_task中にsite.queueへ溜まった新規リンクをSQSへ転送する(worker自身はローカルキューを使わない)。"""
    while True:
        next_task = site.pop_next_task()
        if next_task is None:
            break
        task_queue.send_task(
            url=next_task.url,
            depth=next_task.depth,
            domain=site.domain,
            discovered_from=next_task.discovered_from,
            delay_seconds=int(site.crawl_delay),
        )


async def _process_message(
    message: dict,
    *,
    task_queue: TaskQueue,
    site_cache: DomainSiteCache,
    dedup_store,
    session: httpx.AsyncClient,
    max_depth: int,
) -> None:
    domain = message["domain"]

    def _make_site() -> SiteState:
        return SiteState(domain=domain, start_urls=[], max_depth=max_depth, dedup_store=dedup_store)

    site = site_cache.get_or_create(domain, _make_site)

    try:
        await ensure_robots(site)
        task = URLTask(url=message["url"], depth=message["depth"], discovered_from=message["discovered_from"])
        await run_url_task(site, task, session)
        site_cache.note_task_processed(domain)
        await _forward_discovered_tasks(site, task_queue)
    except Exception as exc:  # noqa: BLE001
        # メッセージを削除しない=visibility timeout後にSQSが自動で再配信する
        print(
            f"[WORKER][ERROR] processing failed domain={domain} url={message.get('url')}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return

    task_queue.delete_task(message["receipt_handle"])


async def run_worker(*, max_concurrency: int = 4) -> None:
    """SQSからURLタスクを受信し続け、クロールして新規リンクを再投入するConsumerループ。"""
    task_queue = get_task_queue()
    dedup_store = _get_dedup_store_or_none()
    site_cache = DomainSiteCache()
    max_depth = _env_int("ETL_WORKER_MAX_DEPTH", 5)
    httpx_timeout_sec = _env_int("ETL_HTTPX_TIMEOUT_SEC", 30)

    print(f"[WORKER] start queue_url={task_queue.queue_url} max_concurrency={max_concurrency}", flush=True)

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx_timeout_sec,
        follow_redirects=True,
    ) as session:
        while True:
            messages = task_queue.receive_tasks(max_messages=max_concurrency, wait_time_seconds=20)
            if not messages:
                continue
            await asyncio.gather(
                *(
                    _process_message(
                        message,
                        task_queue=task_queue,
                        site_cache=site_cache,
                        dedup_store=dedup_store,
                        session=session,
                        max_depth=max_depth,
                    )
                    for message in messages
                )
            )


if __name__ == "__main__":
    asyncio.run(run_worker())
