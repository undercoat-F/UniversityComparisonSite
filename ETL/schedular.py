import asyncio
import ast
import json
import os
import psycopg2
import time
from dataclasses import dataclass
from datetime import datetime
import traceback
from urllib.parse import urlparse

from dotenv import load_dotenv

from ETL.dispatcher import run_dispatcher
from ETL.resource_recorder import start_resource_monitor, stop_resource_monitor
from db.schema_config import get_observer_schema, get_public_schema, get_table_ref, set_search_path

load_dotenv(encoding="utf-8-sig")

SEED_URLS_TABLE = get_table_ref("SEED_URLS_TABLE")
UNIVERSITIES_TABLE = get_table_ref("UNIVERSITIES_TABLE")


# (URL, depth) tuple list
URL_LIST_PATH = os.path.join("ETL", "URLs.txt")
#スキップする時間の設定（月単位）
RECENT_SKIP_MONTHS_ENV = "ETL_RECENT_SKIP_MONTHS"

def get_db_params():
	"""PostgreSQL 接続パラメータを .env から取得"""
	required_keys = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"]
	missing = [k for k in required_keys if not os.getenv(k)]
	if missing:
		raise EnvironmentError(f".env に必須環境変数が未設定: {missing}")
	
	return {
		"host": os.getenv("DB_HOST"),
		"dbname": os.getenv("DB_NAME"),
		"user": os.getenv("DB_USER"),
		"password": os.getenv("DB_PASSWORD"),
		"port": int(os.getenv("DB_PORT", "5432")),
	}


def write_etl_error_log(url, stage, exc, log_dt):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs("log", exist_ok=True)
    with open(f"log/etl_error_log_{log_dt}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{ts}] stage={stage} url={url} error={type(exc).__name__}: {exc}\n")
        f.write(traceback.format_exc())
        f.write("\n")


def write_etl_error_message(url, stage, message, log_dt):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs("log", exist_ok=True)
    with open(f"log/etl_error_log_{log_dt}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{ts}] stage={stage} url={url} error=RuntimeError: {message}\n")


def load_targets_from_db():
    """PostgreSQL から enabled=1 の seed_urls を読み込む"""

    query = f"""
        SELECT root_url, depth
        FROM {SEED_URLS_TABLE}
        WHERE enabled = 1
        ORDER BY id
    """
    try:
        db_params = get_db_params()
        with psycopg2.connect(**db_params) as conn:
            cursor = conn.cursor()
            set_search_path(cursor, get_observer_schema(), get_public_schema())
            cursor.execute(query)
            rows = cursor.fetchall()
            return [(str(url), int(depth)) for url, depth in rows]
    except psycopg2.Error as e:
        print(f"[WARN] seed_urls 読み込み失敗: {e}")
        return []


def load_targets_from_txt(path=URL_LIST_PATH):
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as f:
        targets = []
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("("):
                continue

            tuple_expr = line[:-1] if line.endswith(",") else line
            try:
                item = ast.literal_eval(tuple_expr)
            except Exception:
                continue

            if isinstance(item, tuple) and len(item) == 2:
                targets.append((str(item[0]), int(item[1])))
    return targets


def load_targets():
    targets = load_targets_from_db()
    if targets:
        return targets
    return load_targets_from_txt()


def _normalize_domain(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""

    parsed = urlparse(text if "://" in text else f"https://{text}")
    domain = (parsed.netloc or parsed.path).strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_recent_university_domains_from_db(months: int) -> set[str]:
    dsn = (os.getenv("PARENT_DB_OWNER_CONNECTION") or "").strip()

    query = f"""
        SELECT url
        FROM {UNIVERSITIES_TABLE}
        WHERE created_at >= NOW() - make_interval(months => %s)
          AND url IS NOT NULL
          AND url <> ''
    """

    try:
        if dsn:
            conn_ctx = psycopg2.connect(dsn)
        else:
            conn_ctx = psycopg2.connect(**get_db_params())
        with conn_ctx as conn:
            with conn.cursor() as cursor:
                set_search_path(cursor, get_public_schema())
                cursor.execute(query, (months,))
                rows = cursor.fetchall()
    except psycopg2.Error as e:
        print(f"[SCHEDULER][WARN] recent-domain query failed: {e}", flush=True)
        return set()

    domains = {_normalize_domain(str(url)) for (url,) in rows}
    domains.discard("")
    return domains


def filter_targets_by_recent_universities(targets: list[tuple[str, int]], months: int) -> list[tuple[str, int]]:
    if months <= 0:
        return targets

    recent_domains = load_recent_university_domains_from_db(months)
    if not recent_domains:
        return targets

    filtered: list[tuple[str, int]] = []
    skipped = 0
    for url, depth in targets:
        domain = _normalize_domain(url)
        if domain and domain in recent_domains:
            skipped += 1
            continue
        filtered.append((url, depth))

    print(
        f"[SCHEDULER] recent-domain filter months={months} "
        f"before={len(targets)} skipped={skipped} after={len(filtered)}",
        flush=True,
    )
    return filtered


def summarize_extractions(site_states):
    total_records = sum(
        getattr(site, "extracted_record_count_total", len(site.extracted_records))
        for site in site_states
    )
    total_degrees = sum(
        getattr(site, "extracted_degree_count_total", 0)
        for site in site_states
    )
    if total_degrees == 0:
        for site in site_states:
            for record in site.extracted_records:
                total_degrees += len(record.get("degrees", []))
    return total_records, total_degrees


@dataclass
class JsonlRecordWriter:
    record_fp: object
    counters: dict[str, int]
    max_queue_size: int = 1000

    def __post_init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=self.max_queue_size)
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            line = await self.queue.get()
            try:
                if line is None:
                    return
                await asyncio.to_thread(self.record_fp.write, line)
            finally:
                self.queue.task_done()

    async def write_record(self, domain: str, record: dict) -> None:
        degrees = record.get("degrees", [])
        degree_count = len(degrees) if isinstance(degrees, list) else 0
        payload = {
            "domain": domain,
            "url": record.get("url"),
            "title": record.get("title"),
            "timestamp": record.get("timestamp"),
            "country": record.get("country"),
            "degree_count": degree_count,
            "degrees": degrees if isinstance(degrees, list) else [],
        }
        await self.queue.put(json.dumps(payload, ensure_ascii=False) + "\n")
        self.counters["records"] += 1
        self.counters["degrees"] += degree_count

    async def close(self) -> None:
        await self.queue.join()
        await self.queue.put(None)
        if self.task is not None:
            await self.task


def _build_record_sink(writer: JsonlRecordWriter):
    async def _record_sink(domain: str, record: dict):
        await writer.write_record(domain, record)

    return _record_sink


def _write_crawl_start_log(log_dt: str, started_at: str, target_count: int) -> None:
    with open(f"log/crawl_log_{log_dt}.txt", "a", encoding="utf-8") as f:
        f.write(f"\n{'#'*10}\n")
        f.write(f"# ETL start: {started_at} ({target_count} sites)\n")
        f.write(f"{'#'*10}\n\n")


def _write_crawl_finish_log(
    log_dt: str,
    finished_at: str,
    total_records: int,
    total_degrees: int,
    jsonl_path: str,
    summary_path: str | None,
) -> None:
    with open(f"log/crawl_log_{log_dt}.txt", "a", encoding="utf-8") as f:
        f.write(f"{'#'*10}\n")
        f.write(f"# ETL finished: {finished_at}\n")
        f.write(f"# extracted records: {total_records} / extracted degrees: {total_degrees}\n")
        f.write(f"# extracted JSONL: {jsonl_path}\n")
        if summary_path:
            f.write(f"# extracted summary: {summary_path}\n")
        f.write(f"{'#'*10}\n\n")


def _write_summary_file(summary_path: str, site_states, total_records: int, total_degrees: int) -> None:
    with open(summary_path, "w", encoding="utf-8") as sf:
        sf.write(f"records={total_records}\n")
        sf.write(f"degrees={total_degrees}\n")
        for site in site_states:
            sf.write(
                f"domain={site.domain} records={site.extracted_record_count_total} "
                f"success={site.success_count} errors={site.error_count} "
                f"fallback={site.fallback_count} sitemap_candidates={site.sitemap_candidate_count}\n"
            )


def build_crawl_summary(
    site_states,
    *,
    target_url_count: int,
    total_records: int,
    total_degrees: int,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
) -> dict:
    domains = []
    for site in sorted(site_states, key=lambda item: item.domain):
        successful_requests = site.success_count
        failed_requests = site.error_count
        domains.append({
            "domain": site.domain,
            "visited_url_count": successful_requests + failed_requests,
            "unique_explored_url_count": site.visited_count_total,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "extracted_records": site.extracted_record_count_total,
            "extracted_degrees": site.extracted_degree_count_total,
            "request_elapsed_seconds": round(site.total_time, 3),
        })

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "target_url_count": target_url_count,
        "visited_url_count": sum(item["visited_url_count"] for item in domains),
        "unique_explored_url_count": sum(item["unique_explored_url_count"] for item in domains),
        "successful_requests": sum(item["successful_requests"] for item in domains),
        "failed_requests": sum(item["failed_requests"] for item in domains),
        "extracted_records": total_records,
        "extracted_degrees": total_degrees,
        "domains": domains,
    }


def write_crawl_summary_file(summary_path: str, summary: dict) -> None:
    with open(summary_path, "w", encoding="utf-8") as sf:
        json.dump(summary, sf, ensure_ascii=False, indent=2)
        sf.write("\n")


def _build_error_sink(log_dt: str):
    def _error_sink(domain: str, message: str):
        write_etl_error_message(domain, "crawl", message, log_dt)

    return _error_sink


async def run_etl(*, persist_summary: bool = True):
    log_dt = datetime.now().strftime("%Y%m%d_%H%M%S")
    targets = load_targets()
    skip_months = _env_int(RECENT_SKIP_MONTHS_ENV, 6)
    targets = filter_targets_by_recent_universities(targets, skip_months)
    ts_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_started_at = time.monotonic()
    print(f"[SCHEDULER] ETL start targets={len(targets)}", flush=True)
    os.makedirs("log", exist_ok=True)
    jsonl_path = os.path.join("log", f"extracted_records_{log_dt}.jsonl")
    summary_path = os.path.join("log", f"extracted_summary_{log_dt}.txt") if persist_summary else None
    crawl_summary_path = os.path.join("log", f"crawl_summary_{log_dt}.json")
    counters = {"records": 0, "degrees": 0}

    _write_crawl_start_log(log_dt=log_dt, started_at=ts_start, target_count=len(targets))

    error_buffer_limit = _env_int("ETL_ERROR_BUFFER_LIMIT", 100)
    retain_extracted_records = (_env_int("ETL_RETAIN_EXTRACTED_RECORDS", 0) != 0)

    monitor_state = start_resource_monitor(log_dt)
    queue_size = max(1, _env_int("ETL_RECORD_QUEUE_MAXSIZE", 1000))
    with open(jsonl_path, "w", encoding="utf-8") as record_fp:
        record_writer = JsonlRecordWriter(record_fp, counters, max_queue_size=queue_size)
        await record_writer.start()
        record_sink = _build_record_sink(record_writer)
        error_sink = _build_error_sink(log_dt)

        try:
            site_states = await run_dispatcher(
                targets,
                record_sink=record_sink,
                error_sink=error_sink,
                error_buffer_limit=error_buffer_limit,
                retain_extracted_records=retain_extracted_records,
            )
        except Exception as e:
            write_etl_error_log("ALL", "dispatcher", e, log_dt)
            raise
        finally:
            await record_writer.close()
            stop_resource_monitor(monitor_state)

    for site in site_states:
        print(
            "[SCHEDULER] "
            f"domain={site.domain} success={site.success_count} errors={site.error_count} "
            f"fallback={site.fallback_count} visited={site.visited_count_total} "
            f"sitemap_candidates={site.sitemap_candidate_count}"
        )

    total_records = counters["records"]
    total_degrees = counters["degrees"]
    if total_records == 0 and retain_extracted_records:
        total_records, total_degrees = summarize_extractions(site_states)

    if summary_path:
        _write_summary_file(summary_path, site_states, total_records, total_degrees)

    ts_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    crawl_summary = build_crawl_summary(
        site_states,
        target_url_count=len(targets),
        total_records=total_records,
        total_degrees=total_degrees,
        started_at=ts_start,
        finished_at=ts_end,
        elapsed_seconds=time.monotonic() - run_started_at,
    )
    write_crawl_summary_file(crawl_summary_path, crawl_summary)
    _write_crawl_finish_log(
        log_dt=log_dt,
        finished_at=ts_end,
        total_records=total_records,
        total_degrees=total_degrees,
        jsonl_path=jsonl_path,
        summary_path=summary_path,
    )

    print("\nScheduler run completed")
    print(f"Error details: log/etl_error_log_{log_dt}.txt")
    if jsonl_path:
        print(f"Extracted JSONL: {jsonl_path}")
    if summary_path:
        print(f"Extracted summary: {summary_path}")
    print(f"Crawl summary: {crawl_summary_path}")
    if monitor_state:
        print(f"Resource log: {monitor_state[2]}")

    return {
        "log_dt": log_dt,
        "jsonl_path": jsonl_path,
        "summary_path": summary_path,
        "crawl_summary_path": crawl_summary_path,
        "total_records": total_records,
        "total_degrees": total_degrees,
        "resource_log_path": monitor_state[2] if monitor_state else None,
        "site_states": site_states,
    }

if __name__ == "__main__":
    asyncio.run(run_etl())

