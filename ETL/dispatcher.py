from __future__ import annotations

import asyncio
import gc
import os
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import httpx

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency fallback
    psutil = None

from crawler.crawlworker import DEFAULT_HEADERS, seed_sitemap_candidates, worker
from dataclass.dataclass import QueueBudget, SiteState
from ETL.queue_log import QueueLogStore


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass
class MemoryPressureController:
    high_watermark_mb: int
    resume_watermark_mb: int
    is_paused: bool = False

    def should_pause(self, rss_mb: float) -> tuple[bool, bool]:
        """Return whether dispatch must pause and whether the state changed."""
        was_paused = self.is_paused
        if self.is_paused:
            self.is_paused = rss_mb > self.resume_watermark_mb
        else:
            self.is_paused = rss_mb >= self.high_watermark_mb
        return self.is_paused, self.is_paused != was_paused


def _memory_pressure_controller() -> tuple[MemoryPressureController | None, float]:
    if psutil is None:
        return None, 0.0

    high_watermark_mb = _env_int("ETL_MEMORY_HIGH_WATERMARK_MB", 350, 0)
    if high_watermark_mb <= 0:
        return None, 0.0

    default_resume_mb = max(0, high_watermark_mb - 50)
    resume_watermark_mb = _env_int(
        "ETL_MEMORY_RESUME_WATERMARK_MB", default_resume_mb, 0
    )
    resume_watermark_mb = min(resume_watermark_mb, high_watermark_mb - 1)
    check_interval_sec = float(_env_int("ETL_MEMORY_CHECK_INTERVAL_SEC", 5))
    return MemoryPressureController(high_watermark_mb, resume_watermark_mb), check_interval_sec


async def _seed_sitemaps_with_limits(sites: list[SiteState]) -> tuple[int, int]:
    seeding_concurrency = _env_int("ETL_SITEMAP_SEED_CONCURRENCY", 12)
    per_site_timeout = _env_int("ETL_SITEMAP_SEED_SITE_TIMEOUT_SEC", 90)
    progress_every = _env_int("ETL_SITEMAP_SEED_PROGRESS_EVERY", 20)

    sem = asyncio.Semaphore(seeding_concurrency)

    async def _one(site: SiteState):
        async with sem:
            try:
                await asyncio.wait_for(seed_sitemap_candidates(site), timeout=per_site_timeout)
                return site.domain, None
            except Exception as exc:  # noqa: BLE001
                return site.domain, exc

    tasks = [asyncio.create_task(_one(site)) for site in sites]
    completed = 0
    failed = 0
    by_domain = {site.domain: site for site in sites}

    for task in asyncio.as_completed(tasks):
        domain, exc = await task
        completed += 1
        if exc is not None:
            failed += 1
            site = by_domain.get(domain)
            msg = f"sitemap_seed_failed domain={domain}: {type(exc).__name__}: {exc}"
            if site is not None:
                site.add_error(msg)
            print(f"[DISPATCHER][WARN] {msg}", flush=True)

        if completed % progress_every == 0 or completed == len(tasks):
            print(
                f"[DISPATCHER] sitemap seeding progress done={completed}/{len(tasks)} failed={failed}",
                flush=True,
            )

    seeded_total = sum(site.sitemap_candidate_count for site in sites)
    return seeded_total, failed

def build_site_states(targets: Iterable[tuple[str, int]], *, enqueue_budget: QueueBudget | None = None) -> list[SiteState]:
    grouped: dict[str, list[tuple[str, int]]] = {}
    for url, depth in targets:
        domain = urlparse(url).netloc
        grouped.setdefault(domain, []).append((url, depth))

    sites: list[SiteState] = []
    for domain, entries in grouped.items():
        start_urls = [url for url, _ in entries]
        max_depth = max(depth for _, depth in entries)
        sites.append(SiteState(domain=domain, start_urls=start_urls, max_depth=max_depth, enqueue_budget=enqueue_budget))
    return sites


def _active_sites(sites: list[SiteState]) -> list[SiteState]:
    return [site for site in sites if site.status == "active" and site.has_pending()]


def _progress_snapshot(sites: list[SiteState], root_target_total: int) -> dict[str, int | float]:
    visited_total = sum(site.visited_count_total for site in sites)
    pending_total = sum(len(site.queue) for site in sites)
    in_progress_total = sum(len(site.in_progress_urls) for site in sites)
    success_total = sum(site.success_count for site in sites)
    error_total = sum(site.error_count for site in sites)
    completed_total = success_total + error_total
    known_total = visited_total + pending_total
    root_visited = sum(site.root_visited_count for site in sites)
    known_progress_pct = (completed_total / known_total * 100.0) if known_total > 0 else 100.0
    root_progress_pct = (root_visited / root_target_total * 100.0) if root_target_total > 0 else 100.0
    return {
        "root_target_total": root_target_total,
        "root_visited": root_visited,
        "visited_total": visited_total,
        "pending_total": pending_total,
        "in_progress_total": in_progress_total,
        "success_total": success_total,
        "error_total": error_total,
        "completed_total": completed_total,
        "known_total": known_total,
        "known_progress_pct": known_progress_pct,
        "root_progress_pct": root_progress_pct,
    }


def _stuck_site_lines(sites: list[SiteState], limit: int = 5) -> list[str]:
    ranked = sorted(
        (site for site in sites if site.status == "active" and site.has_pending()),
        key=lambda s: len(s.queue),
        reverse=True,
    )
    lines: list[str] = []
    now = time.time()
    for site in ranked[: max(1, limit)]:
        last_access_age = max(0.0, now - site.last_access) if site.last_access > 0 else -1.0
        lines.append(
            "domain={domain} pending={pending} in_progress={in_progress} "
            "ready_in={ready_in:.2f}s crawl_delay={crawl_delay:.2f}s last_access_age={last_access_age:.2f}s "
            "visited={visited} success={success} error={error}".format(
                domain=site.domain,
                pending=len(site.queue),
                in_progress=len(site.in_progress_urls),
                ready_in=site.seconds_until_ready(),
                crawl_delay=site.crawl_delay,
                last_access_age=last_access_age,
                visited=site.visited_count_total,
                success=site.success_count,
                error=site.error_count,
            )
        )
    return lines


async def run_dispatcher(
    targets: list[tuple[str, int]],
    *,
    max_active_sites: int = 4,
    timeout_sec: int = 30,
    progress_interval_sec: int = 10,
    queue_log_enabled: bool = True,
    record_sink=None,
    error_sink=None,
    error_buffer_limit: int = 200,
    retain_extracted_records: bool = True,
) -> list[SiteState]:
    """crawl-delay 中のサイトは待機し、ready な他ドメインを進める。"""
    # timeout_sec は httpx リクエストのタイムアウト秒。
    httpx_timeout_sec = _env_int("ETL_HTTPX_TIMEOUT_SEC", timeout_sec)
    worker_timeout_sec = _env_int("ETL_WORKER_TIMEOUT_SEC", 120)
    dispatcher_timeout_sec = _env_int("ETL_DISPATCHER_TIMEOUT_SEC", 0, 0)
    domain_timeout_sec = _env_int("ETL_DOMAIN_MAX_SECONDS", 0, 0)
    stall_guard_sec = _env_int("ETL_STALL_GUARD_SEC", 900, 0)
    stall_site_sample = _env_int("ETL_STALL_SITE_SAMPLE", 5)
    memory_pressure_max_wait_sec = _env_int(
        "ETL_MEMORY_PRESSURE_MAX_WAIT_SEC", 300, 0
    )

    pending_queue_limit = _env_int("ETL_MAXPENDING_QUEUE_ITEMS", 2000)
    memory_controller, memory_check_interval_sec = _memory_pressure_controller()
    process = psutil.Process(os.getpid()) if memory_controller is not None else None
    enqueue_budget = QueueBudget(limit=pending_queue_limit)
    sites = build_site_states(targets, enqueue_budget=enqueue_budget)
    for site in sites:
        site.record_sink = record_sink
        site.error_sink = error_sink
        site.error_buffer_limit = max(0, int(error_buffer_limit))
        site.retain_extracted_records = retain_extracted_records

    root_target_total = len(targets)
    if not sites:
        return sites

    queue_logger = None
    run_id = None
    queue_log_enabled = queue_log_enabled and _env_flag("QUEUE_LOG_ENABLED", True)
    if queue_log_enabled:
        queue_log_batch_size = int(os.getenv("QUEUE_LOG_BATCH_SIZE", "200"))
        queue_log_failures_only = os.getenv("QUEUE_LOG_FAILURES_ONLY", "1") not in {"0", "false", "False"}
        queue_log_pg_dsn = os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
        if not queue_log_pg_dsn:
            required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
            if all((os.getenv(name, "").strip() for name in required)):
                host = os.getenv("DB_HOST", "").strip()
                dbname = os.getenv("DB_NAME", "").strip()
                user = os.getenv("DB_USER", "").strip()
                password = os.getenv("DB_PASSWORD", "").strip()
                port = os.getenv("DB_PORT", "5432").strip() or "5432"
                queue_log_pg_dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"
        schema_path = os.getenv("QUEUE_LOG_SCHEMA_PATH", os.path.join("ETL", "queue_log_schema.sql"))

        if not queue_log_pg_dsn:
            print(
                "[QUEUE_LOG] disabled: PARENT_DB_OWNER_CONNECTION or DB_* is not set",
                flush=True,
            )
            queue_log_enabled = False

        if queue_log_enabled:
            queue_logger = QueueLogStore(
                db_path="",
                schema_path=schema_path,
                pg_dsn=queue_log_pg_dsn,
                batch_size=queue_log_batch_size,
                failures_only=queue_log_failures_only,
            )
            queue_logger.init_db()
            run_id = queue_logger.create_run(root_seed_count=len(targets), notes="dispatcher run")
            for site in sites:
                site.queue_logger = queue_logger
                site.run_id = run_id

    started_at = time.monotonic()
    domain_started_at: dict[str, float | None] = {site.domain: None for site in sites}
    last_progress_at = started_at
    stalled_since: float | None = None
    memory_pressure_since: float | None = None
    last_completed_total = -1
    last_visited_total = -1
    last_pending_total = -1

    print(
        "[DISPATCHER] start "
        f"sites={len(sites)} "
        f"httpx_timeout={httpx_timeout_sec}s "
        f"worker_timeout={worker_timeout_sec}s "
        f"dispatcher_timeout={(str(dispatcher_timeout_sec) + 's') if dispatcher_timeout_sec > 0 else 'disabled'} "
        f"domain_timeout={(str(domain_timeout_sec) + 's') if domain_timeout_sec > 0 else 'disabled'} "
        f"stall_guard={(str(stall_guard_sec) + 's') if stall_guard_sec > 0 else 'disabled'} "
        f"pending_limit={pending_queue_limit} "
        f"memory_limit={(str(memory_controller.high_watermark_mb) + 'MB') if memory_controller else 'disabled'}",
        flush=True,
    )

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx_timeout_sec,
        follow_redirects=True,
    ) as session:
        run_status = "completed"
        run_notes = ""
        try:
            print("[DISPATCHER] seeding sitemap candidates...", flush=True)
            seeded_total, seeding_failed = await _seed_sitemaps_with_limits(sites)
            print(
                "[DISPATCHER] sitemap seeding done "
                f"candidates={seeded_total} failed={seeding_failed} elapsed={int(time.monotonic() - started_at)}s",
                flush=True,
            )

            while True:
                now = time.monotonic()

                if domain_timeout_sec > 0:
                    for site in sites:
                        if site.status != "active":
                            continue
                        if domain_started_at.get(site.domain) is None and (
                            site.visited_count_total > 0 or site.in_progress_urls
                        ):
                            domain_started_at[site.domain] = now

                if domain_timeout_sec > 0:
                    for site in sites:
                        if site.status != "active":
                            continue
                        started = domain_started_at.get(site.domain)
                        if started is None:
                            continue
                        elapsed = now - started
                        if elapsed < domain_timeout_sec:
                            continue
                        dropped_pending = len(site.queue)
                        while site.queue:
                            site.pop_next_task()
                        site.status = "stopped"
                        site.add_error(
                            f"domain_timeout domain={site.domain} elapsed={int(elapsed)}s limit={domain_timeout_sec}s dropped_pending={dropped_pending}"
                        )
                        print(
                            "[DISPATCHER][WARN] "
                            f"domain_timeout domain={site.domain} elapsed={int(elapsed)}s "
                            f"limit={domain_timeout_sec}s dropped_pending={dropped_pending}",
                            flush=True,
                        )

                snap = _progress_snapshot(sites, root_target_total)

                completed_total = int(snap["completed_total"])
                visited_total = int(snap["visited_total"])
                pending_total = int(snap["pending_total"])
                in_progress_total = int(snap["in_progress_total"])

                if pending_total > 0 and in_progress_total == 0:
                    no_state_change = (
                        completed_total == last_completed_total
                        and visited_total == last_visited_total
                        and pending_total == last_pending_total
                    )
                    if no_state_change:
                        if stalled_since is None:
                            stalled_since = now
                        elif stall_guard_sec > 0 and now - stalled_since >= stall_guard_sec:
                            run_status = "stalled"
                            run_notes = (
                                f"stall_guard_triggered elapsed={int(now - started_at)}s "
                                f"stall_for={int(now - stalled_since)}s "
                                f"root={snap['root_visited']}/{snap['root_target_total']}({snap['root_progress_pct']:.1f}%) "
                                f"known_done={snap['completed_total']}/{snap['known_total']}({snap['known_progress_pct']:.1f}%) "
                                f"pending={pending_total} in_progress={in_progress_total}"
                            )
                            print(f"[DISPATCHER][WARN] {run_notes}", flush=True)
                            for line in _stuck_site_lines(sites, limit=stall_site_sample):
                                print(f"[DISPATCHER][STUCK] {line}", flush=True)
                            break
                    else:
                        stalled_since = None
                else:
                    stalled_since = None

                last_completed_total = completed_total
                last_visited_total = visited_total
                last_pending_total = pending_total

                if dispatcher_timeout_sec > 0 and now - started_at >= dispatcher_timeout_sec:
                    run_status = "timed_out"
                    run_notes = (
                        f"dispatcher_timeout_reached elapsed={int(now - started_at)}s "
                        f"limit={dispatcher_timeout_sec}s "
                        f"root={snap['root_visited']}/{snap['root_target_total']}({snap['root_progress_pct']:.1f}%) "
                        f"known_done={snap['completed_total']}/{snap['known_total']}({snap['known_progress_pct']:.1f}%) "
                        f"pending={snap['pending_total']} in_progress={snap['in_progress_total']}"
                    )
                    print(f"[DISPATCHER][WARN] {run_notes}", flush=True)
                    break

                if now - last_progress_at >= progress_interval_sec:
                    print(
                        "[PROGRESS] "
                        f"elapsed={int(now - started_at)}s "
                        f"root={snap['root_visited']}/{snap['root_target_total']}({snap['root_progress_pct']:.1f}%) "
                        f"known_done={snap['completed_total']}/{snap['known_total']}({snap['known_progress_pct']:.1f}%) "
                        f"visited_total={snap['visited_total']} "
                        f"pending_total={snap['pending_total']} "
                        f"in_progress_total={snap['in_progress_total']} "
                        f"success_total={snap['success_total']} "
                        f"error_total={snap['error_total']}",
                        flush=True,
                    )
                    last_progress_at = now

                active = _active_sites(sites)
                for site in sites:
                    if site.status != "active":
                        continue
                    if site.has_pending() or site.in_progress_urls:
                        continue
                    site.release_runtime_memory()
                if not active:
                    break

                if memory_controller is not None and process is not None:
                    rss_mb = process.memory_info().rss / (1024 * 1024)
                    memory_paused, memory_state_changed = memory_controller.should_pause(rss_mb)
                    if memory_paused:
                        if memory_state_changed:
                            memory_pressure_since = now
                            print(
                                "[DISPATCHER][WARN] "
                                f"memory_high_watermark rss={rss_mb:.1f}MB "
                                f"limit={memory_controller.high_watermark_mb}MB; pausing new workers",
                                flush=True,
                            )
                        elif (
                            memory_pressure_max_wait_sec > 0
                            and memory_pressure_since is not None
                            and now - memory_pressure_since >= memory_pressure_max_wait_sec
                        ):
                            run_status = "stalled"
                            run_notes = (
                                f"memory_pressure_timeout elapsed={int(now - started_at)}s "
                                f"rss={rss_mb:.1f}MB limit={memory_controller.high_watermark_mb}MB "
                                f"waited={int(now - memory_pressure_since)}s "
                                f"pending={pending_total} in_progress={in_progress_total}"
                            )
                            print(f"[DISPATCHER][WARN] {run_notes}", flush=True)
                            for line in _stuck_site_lines(sites, limit=stall_site_sample):
                                print(f"[DISPATCHER][STUCK] {line}", flush=True)
                            break
                        gc.collect()
                        await asyncio.sleep(memory_check_interval_sec)
                        continue
                    memory_pressure_since = None
                    if memory_state_changed:
                        print(
                            "[DISPATCHER] "
                            f"memory_recovered rss={rss_mb:.1f}MB "
                            f"resume={memory_controller.resume_watermark_mb}MB; resuming workers",
                            flush=True,
                        )

                ready = [site for site in active if site.can_fetch()]
                if not ready:
                    sleep_for = min(site.seconds_until_ready() for site in active)
                    await asyncio.sleep(max(0.01, sleep_for))
                    continue

                ready = ready[:max_active_sites]
                worker_tasks = [(site, asyncio.create_task(worker(site, session))) for site in ready]
                done, pending = await asyncio.wait(
                    [task for _, task in worker_tasks],
                    timeout=worker_timeout_sec,
                )

                if pending:
                    for site, task in worker_tasks:
                        if task in pending:
                            site.add_error(
                                f"worker_timeout domain={site.domain} timeout={worker_timeout_sec}s"
                            )
                            task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    print(
                        f"[DISPATCHER][WARN] worker timeout count={len(pending)} timeout={worker_timeout_sec}s",
                        flush=True,
                    )

                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
        except Exception as exc:
            run_status = "failed"
            run_notes = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if queue_logger is not None and run_id is not None:
                try:
                    queue_logger.finish_run(run_id=run_id, status=run_status, notes=run_notes)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[QUEUE_LOG][WARN] could not finalize run_id={run_id}: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                try:
                    queue_logger.close()
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[QUEUE_LOG][WARN] could not close queue logger: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )

    return sites