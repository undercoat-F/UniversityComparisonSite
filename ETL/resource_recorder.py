from __future__ import annotations

import csv
import os
import socket
import threading
import time
from datetime import datetime

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency fallback
    psutil = None

try:
    import psycopg2
except ImportError:  # pragma: no cover - postgres is optional
    psycopg2 = None

from db.schema_config import get_etl_schema, get_public_schema, get_table_ref, set_search_path


RESOURCE_MONITOR_ENABLED_ENV = "ETL_RESOURCE_MONITOR_ENABLED"
RESOURCE_MONITOR_INTERVAL_ENV = "ETL_RESOURCE_MONITOR_INTERVAL_SEC"
RESOURCE_DB_ENABLED_ENV = "ETL_RESOURCE_DB_ENABLED"
RESOURCE_DB_INTERVAL_ENV = "ETL_RESOURCE_DB_INTERVAL_SEC"
RESOURCE_DB_BATCH_SIZE_ENV = "ETL_RESOURCE_DB_BATCH_SIZE"

# テーブル定義は ETL/resource_monitor.sql をNeonで実行して作成済み前提


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_pg_dsn() -> str:
    dsn = os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
    if dsn:
        return dsn
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if not all(os.getenv(name, "").strip() for name in required):
        return ""
    host = os.getenv("DB_HOST", "").strip()
    dbname = os.getenv("DB_NAME", "").strip()
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "").strip()
    port = os.getenv("DB_PORT", "5432").strip() or "5432"
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"


class _ResourceSampleWriter:
    """一定間隔に間引いたリソースサンプルをバッチでPostgresへ書き込む。"""

    def __init__(self, pg_dsn: str, table_ref: str, batch_size: int) -> None:
        self._pg_dsn = pg_dsn
        self._table_ref = table_ref
        self._batch_size = max(1, batch_size)
        self._buffer: list[tuple] = []
        self._conn = None
        self._init_failed = False

    def _connect(self):
        if self._conn is not None and getattr(self._conn, "closed", 1) == 0:
            return self._conn
        self._conn = psycopg2.connect(self._pg_dsn)
        self._conn.autocommit = False
        with self._conn.cursor() as cursor:
            set_search_path(cursor, get_etl_schema(), get_public_schema())
        return self._conn

    def add(self, row: tuple) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        rows = self._buffer
        try:
            conn = self._connect()
            with conn.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {self._table_ref}
                    (log_dt, worker_id, elapsed_sec, process_cpu_percent, process_rss_mb,
                     system_cpu_percent, system_memory_percent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()
            self._buffer = []
        except Exception as exc:  # noqa: BLE001
            if self._conn is not None:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            print(f"[MONITOR][WARN] resource sample flush failed: {type(exc).__name__}: {exc}", flush=True)

    def close(self) -> None:
        self.flush()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _build_resource_sample_writer() -> "_ResourceSampleWriter | None":
    if not _env_bool(RESOURCE_DB_ENABLED_ENV, True):
        return None
    if psycopg2 is None:
        print("[MONITOR] psycopg2 is not installed; resource DB logging disabled", flush=True)
        return None
    pg_dsn = _resolve_pg_dsn()
    if not pg_dsn:
        print("[MONITOR] resource DB logging disabled: PARENT_DB_OWNER_CONNECTION or DB_* is not set", flush=True)
        return None
    try:
        table_ref = get_table_ref("CRAWL_RESOURCE_SAMPLES_TABLE")
    except KeyError:
        print(
            "[MONITOR] resource DB logging disabled: CRAWL_RESOURCE_SAMPLES_TABLE is not set",
            flush=True,
        )
        return None
    batch_size = _env_int(RESOURCE_DB_BATCH_SIZE_ENV, 10)
    return _ResourceSampleWriter(pg_dsn=pg_dsn, table_ref=table_ref, batch_size=batch_size)


def start_resource_monitor(log_dt: str):
    if not _env_bool(RESOURCE_MONITOR_ENABLED_ENV, True):
        print("[MONITOR] resource monitoring disabled by env", flush=True)
        return None
    if psutil is None:
        print("[MONITOR] psutil is not installed; resource monitoring disabled", flush=True)
        return None

    interval_sec = max(0.5, _env_float(RESOURCE_MONITOR_INTERVAL_ENV, 5.0))
    # DBへの書き込みはCSVより粗い間隔に間引く(既定30秒)。長時間トレンドの把握には十分で、書き込み回数を抑えられる。
    db_interval_sec = max(interval_sec, _env_float(RESOURCE_DB_INTERVAL_ENV, 30.0))
    worker_id = os.getenv("WORKER_ID", "").strip() or socket.gethostname()
    sample_writer = _build_resource_sample_writer()

    monitor_path = os.path.join("log", f"etl_resource_log_{log_dt}.csv")
    os.makedirs("log", exist_ok=True)

    process = psutil.Process(os.getpid())
    process.cpu_percent(None)
    psutil.cpu_percent(None)
    stop_event = threading.Event()

    with open(monitor_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp",
                "elapsed_sec",
                "process_cpu_percent",
                "process_rss_mb",
                "system_cpu_percent",
                "system_memory_percent",
            ]
        )

    def _run_monitor() -> None:
        started_at = time.perf_counter()
        last_db_write_at = started_at - db_interval_sec  # 初回サンプルも書き込む
        with open(monitor_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            while not stop_event.wait(interval_sec):
                try:
                    now = time.perf_counter()
                    elapsed_sec = now - started_at
                    process_cpu_percent = process.cpu_percent(None)
                    process_rss_mb = process.memory_info().rss / (1024 * 1024)#　1024*1024 はMB変換
                    system_cpu_percent = psutil.cpu_percent(None)
                    system_memory_percent = psutil.virtual_memory().percent
                    row = [
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        f"{elapsed_sec:.1f}",
                        f"{process_cpu_percent:.1f}",
                        f"{process_rss_mb:.1f}",
                        f"{system_cpu_percent:.1f}",
                        f"{system_memory_percent:.1f}",
                    ]
                    writer.writerow(row)
                    f.flush()

                    if sample_writer is not None and now - last_db_write_at >= db_interval_sec:
                        sample_writer.add(
                            (
                                log_dt,
                                worker_id,
                                elapsed_sec,
                                process_cpu_percent,
                                process_rss_mb,
                                system_cpu_percent,
                                system_memory_percent,
                            )
                        )
                        last_db_write_at = now
                except Exception as exc:  # noqa: BLE001
                    writer.writerow(
                        [
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            f"{time.perf_counter() - started_at:.1f}",
                            "monitor_error",
                            type(exc).__name__,
                            str(exc),
                            "",
                        ]
                    )
                    f.flush()
                    break

        if sample_writer is not None:
            sample_writer.close()

    thread = threading.Thread(target=_run_monitor, name="etl-resource-monitor", daemon=True)
    thread.start()
    print(
        f"[MONITOR] resource log -> {monitor_path} (csv_interval={interval_sec}s, "
        f"db_interval={db_interval_sec if sample_writer is not None else 'disabled'}s)",
        flush=True,
    )
    return stop_event, thread, monitor_path


def stop_resource_monitor(monitor_state):
    if not monitor_state:
        return None

    stop_event, thread, monitor_path = monitor_state
    stop_event.set()
    thread.join(timeout=2)
    print(f"[MONITOR] resource log finalized -> {monitor_path}", flush=True)
    return monitor_path
