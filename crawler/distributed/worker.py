from __future__ import annotations

import asyncio
import functools
import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
from dotenv import load_dotenv

from crawler.crawlworker import DEFAULT_HEADERS, ensure_robots, run_url_task
from crawler.distributed.dedup_store import get_dedup_store
from crawler.distributed.domain_rate_limiter import get_domain_rate_limiter
from crawler.distributed.record_writer import DbRecordLoader, RecordBatchWriter, record_db_enabled
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
    # EC2(コンテナ)ごとに集計できるよう、再起動しても変わらないhostnameのみを付与する。
    configured = os.getenv("WORKER_ID", "").strip()
    if configured:
        return f"{configured}:{socket.gethostname()}"
    return socket.gethostname()


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
        )


def _extend_visibility_if_needed(task_queue: TaskQueue, message: dict, wait_sec: float) -> None:
    """待機と処理が終わる前に可視性タイムアウトが切れそうなら延長する。

    切れると他の Worker に再配信され、同じURLが二重に処理される。
    """
    deadline = message.get("visible_deadline")
    if deadline is None:
        return
    allowance = _env_int("ETL_WORKER_TIMEOUT_SEC", 120)
    now = time.monotonic()
    if now + wait_sec + allowance <= deadline:
        return
    timeout = int(wait_sec + allowance) + 1
    task_queue.extend_visibility(message["receipt_handle"], timeout)
    message["visible_deadline"] = now + timeout


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
    record_sink=None,
    rate_limiter=None,
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
            record_sink=record_sink,
            retain_extracted_records=False,
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
        if rate_limiter is not None:
            # Crawl-delay を Worker 間で守るため、ドメインのアクセス枠を予約してから取得する
            wait_sec = await rate_limiter.reserve(domain, site.crawl_delay)
            if wait_sec > 0:
                _extend_visibility_if_needed(task_queue, message, wait_sec)
                await asyncio.sleep(wait_sec)
            # 待ち明けが遅れても間隔が縮まないよう、実際の開始時刻を基準に次回時刻を記録する
            await rate_limiter.mark_started(domain, site.crawl_delay)
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


def _record_writer_or_none() -> RecordBatchWriter | None:
    if not record_db_enabled():
        print("[WORKER] record DB writing disabled: RECORD_DB_ENABLED is off", flush=True)
        return None
    try:
        loader = DbRecordLoader()
    except Exception as exc:  # noqa: BLE001
        print(f"[WORKER][WARN] record DB writing disabled: {type(exc).__name__}: {exc}", flush=True)
        return None
    return RecordBatchWriter(
        loader,
        batch_size=_env_int("RECORD_DB_BATCH_SIZE", 200),
        flush_interval_sec=_env_int("RECORD_DB_FLUSH_INTERVAL_SEC", 30),
        max_pending=_env_int("RECORD_DB_MAX_PENDING", 1000),
    )


async def _run_until_first_error(tasks: list[asyncio.Task]) -> None:
    """どれか1つのタスクが例外で終わったら、残りをキャンセルしてその例外を送出する。"""
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.exception() is not None:
                raise task.exception()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_worker(*, max_concurrency: int | None = None) -> None:
    """SQSからURLタスクを受信し、クロールして新規リンクを再投入するConsumerループ。

    「1件ずつ受信して処理する窓口（consumer）」を max_concurrency 個並べ、窓口ごとに独立して回す。
    FIFOキューは処理中のメッセージがあるグループ（ドメイン）のメッセージを他の受信に渡さないため、
    各窓口には自動的に別々のドメインが届き、同じドメインへの同時アクセスも起きない。
    （以前は最大4件をまとめて受信していたが、同じドメインがまとめて届き、ドメインが順番に処理されていた）
    """
    max_concurrency = max_concurrency or _env_int("WORKER_MAX_CONCURRENCY", 4)
    task_queue = get_task_queue()
    dedup_store = _get_dedup_store_or_none()
    worker_id = _worker_id()
    queue_logger = _queue_log_store_or_none(worker_id)
    record_writer = _record_writer_or_none()
    if record_writer is not None:
        await record_writer.start()
    rate_limiter = get_domain_rate_limiter()
    site_cache = DomainSiteCache()
    active_run_ids: set[int] = set()
    max_depth = _env_int("ETL_WORKER_MAX_DEPTH", 5)
    visibility_timeout_sec = _env_int("SQS_VISIBILITY_TIMEOUT_SEC", 180)
    httpx_timeout_sec = _env_int("ETL_HTTPX_TIMEOUT_SEC", 30)
    monitor_state = None

    print(
        f"[WORKER] start worker_id={worker_id} queue_url={task_queue.queue_url} "
        f"max_concurrency={max_concurrency}",
        flush=True,
    )
    # 受信は最大20秒待つ同期処理（boto3）のため別スレッドで行う。共有のスレッドプールを使うと、
    # 待機中の受信で枠が埋まり、Valkey の予約や DB 書き込みなど他の to_thread 処理が詰まるため専用にする
    receive_executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="sqs-receive")
    loop = asyncio.get_running_loop()
    exit_status = "stopped"
    try:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx_timeout_sec,
            follow_redirects=True,
        ) as session:

            async def _consumer(slot: int) -> None:
                nonlocal monitor_state
                while True:
                    messages = await loop.run_in_executor(
                        receive_executor,
                        functools.partial(task_queue.receive_tasks, max_messages=1, wait_time_seconds=20),
                    )
                    if not messages:
                        if slot == 0:
                            print("[WORKER] waiting for messages...", flush=True)
                        continue
                    message = messages[0]
                    message["visible_deadline"] = time.monotonic() + visibility_timeout_sec
                    if monitor_state is None:
                        monitor_state = start_resource_monitor(
                            datetime.now().strftime("%Y%m%d_%H%M%S"),
                            run_id=message["run_id"],
                            worker_id=worker_id,
                        )
                    await _process_message(
                        message,
                        task_queue=task_queue,
                        site_cache=site_cache,
                        dedup_store=dedup_store,
                        queue_logger=queue_logger,
                        worker_id=worker_id,
                        active_run_ids=active_run_ids,
                        session=session,
                        max_depth=max_depth,
                        record_sink=record_writer.add if record_writer is not None else None,
                        rate_limiter=rate_limiter,
                    )

            consumers = [asyncio.create_task(_consumer(slot), name=f"consumer-{slot}") for slot in range(max_concurrency)]
            await _run_until_first_error(consumers)
    except Exception:
        exit_status = "failed"
        raise
    finally:
        # 受信中のスレッドは最大20秒で戻るが、終了処理を待たせないよう待たずに閉じる
        receive_executor.shutdown(wait=False, cancel_futures=True)
        stop_resource_monitor(monitor_state)
        if record_writer is not None:
            await record_writer.close()
        rate_limiter.close()
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


async def _main() -> None:
    # docker stop の SIGTERM でもタスクをキャンセルし、run_worker の終了処理（未書き込みレコードの書き込み）を実行させる
    task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    except (NotImplementedError, AttributeError):  # Windows
        pass
    try:
        await run_worker()
    except asyncio.CancelledError:
        print("[WORKER] stopped by SIGTERM", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
