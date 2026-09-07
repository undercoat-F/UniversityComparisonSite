from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import datetime

import httpx
from dotenv import load_dotenv

from crawler.crawlworker import DEFAULT_HEADERS, ensure_robots, run_url_task
from crawler.distributed.dedup_store import get_dedup_store
from crawler.distributed.site_cache import DomainSiteCache
from crawler.distributed.sqs_queue import TaskQueue, get_task_queue
from dataclass.dataclass import SiteState, URLTask
from ControlPlane.resource_recorder import start_resource_monitor, stop_resource_monitor

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


def _worker_id() -> str:
    configured = os.getenv("WORKER_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _queue_log_store_or_none(worker_id: str):
    dsn = os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
    if not dsn:
        print("[WORKER] DB event logging disabled: PARENT_DB_OWNER_CONNECTION is not set", flush=True)
        return None
    try:
        from ControlPlane.queue_log import QueueLogStore

        return QueueLogStore(
            db_path="",
            schema_path=os.getenv("QUEUE_LOG_SCHEMA_PATH", os.path.join("ControlPlane", "queue_log_schema.sql")),
            pg_dsn=dsn,
            batch_size=_env_int("QUEUE_LOG_BATCH_SIZE", 200),
            failures_only=False,
            worker_id=worker_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[WORKER][WARN] DB event logging disabled: {type(exc).__name__}: {exc}", flush=True)
        return None


async def _forward_discovered_tasks(site: SiteState, task_queue: TaskQueue) -> None:
    while True:
        next_task = site.pop_next_task()
        if next_task is None:
            break
        task_queue.send_task(
            run_id=site.run_id,
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
    queue_logger,
    worker_id: str,
    active_run_ids: set[int],
    session: httpx.AsyncClient,
    max_depth: int,
) -> None:
    domain = message["domain"]
    url = message["url"]
    run_id = message["run_id"]
    print(f"[WORKER] processing domain={domain} url={url}", flush=True)

    def _make_site() -> SiteState:
        return SiteState(
            domain=domain,
            start_urls=[],
            run_id=run_id,
            max_depth=max_depth,
            dedup_store=dedup_store,
            queue_logger=queue_logger,
        )

    site = site_cache.get_or_create(domain, _make_site)
    if queue_logger is not None and run_id not in active_run_ids:
        try:
            queue_logger.start_worker_run(run_id, worker_id)
            active_run_ids.add(run_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[WORKER][WARN] worker run logging failed: {type(exc).__name__}: {exc}", flush=True)
    try:
        await ensure_robots(site)
        task = URLTask(url=url, depth=message["depth"], discovered_from=message["discovered_from"])
        attempt = await run_url_task(site, task, session)
        site_cache.note_task_processed(domain)
        await _forward_discovered_tasks(site, task_queue)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WORKER][ERROR] processing failed domain={domain} url={url}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        if queue_logger is not None:
            try:
                queue_logger.update_worker_run(run_id, worker_id, urls_failed=1)
            except Exception as log_exc:  # noqa: BLE001
                print(f"[WORKER][WARN] worker run update failed: {type(log_exc).__name__}: {log_exc}", flush=True)
        return

    task_queue.delete_task(message["receipt_handle"])
    if queue_logger is not None:
        try:
            queue_logger.update_worker_run(
                run_id,
                worker_id,
                urls_completed=1 if attempt.ok else 0,
                urls_failed=0 if attempt.ok else 1,
                records_extracted=len(attempt.extracted_records),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WORKER][WARN] worker run update failed: {type(exc).__name__}: {exc}", flush=True)
    print(f"[WORKER] completed domain={domain} url={url}", flush=True)


async def run_worker(*, max_concurrency: int = 4) -> None:
    """SQSからURLタスクを受信し、クロールして新規リンクを再投入するConsumerループ。"""
    task_queue = get_task_queue()
    dedup_store = _get_dedup_store_or_none()
    worker_id = _worker_id()
    queue_logger = _queue_log_store_or_none(worker_id)
    site_cache = DomainSiteCache()
    active_run_ids: set[int] = set()
    max_depth = _env_int("ETL_WORKER_MAX_DEPTH", 5)
    httpx_timeout_sec = _env_int("ETL_HTTPX_TIMEOUT_SEC", 30)
    monitor_state = None

    print(
        f"[WORKER] start worker_id={worker_id} queue_url={task_queue.queue_url} "
        f"max_concurrency={max_concurrency}",
        flush=True,
    )
    exit_status = "stopped"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx_timeout_sec,
            follow_redirects=True,
        ) as session:
            while True:
                messages = task_queue.receive_tasks(max_messages=max_concurrency, wait_time_seconds=20)
                if not messages:
                    print("[WORKER] waiting for messages...", flush=True)
                    continue
                if monitor_state is None:
                    monitor_state = start_resource_monitor(
                        datetime.now().strftime("%Y%m%d_%H%M%S"),
                        run_id=messages[0]["run_id"],
                        worker_id=worker_id,
                    )
                print(f"[WORKER] received {len(messages)} message(s)", flush=True)
                await asyncio.gather(
                    *(
                        _process_message(
                            message,
                            task_queue=task_queue,
                            site_cache=site_cache,
                            dedup_store=dedup_store,
                            queue_logger=queue_logger,
                            worker_id=worker_id,
                            active_run_ids=active_run_ids,
                            session=session,
                            max_depth=max_depth,
                        )
                        for message in messages
                    )
                )
    except Exception:
        exit_status = "failed"
        raise
    finally:
        stop_resource_monitor(monitor_state)
        if queue_logger is not None:
            for run_id in active_run_ids:
                try:
                    queue_logger.update_worker_run(run_id, worker_id, status=exit_status)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[WORKER][WARN] could not record worker shutdown: {type(exc).__name__}: {exc}",
                        flush=True,
                    )
            queue_logger.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
