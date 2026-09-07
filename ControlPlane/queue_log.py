from __future__ import annotations

import json
import time
from typing import Any, Optional

try:
    import psycopg2
except Exception:  # pragma: no cover - postgres is optional
    psycopg2 = None

from db.schema_config import get_etl_schema, get_public_schema, get_table_ref, render_sql_template, set_search_path

CRAWL_RUNS_TABLE = get_table_ref("CRAWL_RUNS_TABLE")
CRAWL_QUEUE_STATE_TABLE = get_table_ref("CRAWL_QUEUE_STATE_TABLE")
CRAWL_ATTEMPTS_TABLE = get_table_ref("CRAWL_ATTEMPTS_TABLE")
CRAWL_EDGES_TABLE = get_table_ref("CRAWL_EDGES_TABLE")
CRAWL_FAILURES_TABLE = get_table_ref("CRAWL_FAILURES_TABLE")
CRAWL_TAG_KEYWORD_HITS_TABLE = get_table_ref("CRAWL_TAG_KEYWORD_HITS_TABLE")
CRAWL_DOMAIN_TAG_SCORES_TABLE = get_table_ref("CRAWL_DOMAIN_TAG_SCORES_TABLE")
CRAWL_TAG_CLASS_COUNTS_TABLE = get_table_ref("CRAWL_TAG_CLASS_COUNTS_TABLE")
CRAWL_DOMAIN_CLASS_COUNTS_TABLE = get_table_ref("CRAWL_DOMAIN_CLASS_COUNTS_TABLE")

class QueueLogStore:
    def __init__(
        self,
        db_path: str,
        schema_path: str,
        *,
        pg_dsn: str = "",
        batch_size: int = 100,
        failures_only: bool = True,
        worker_id: str = "",
    ) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
        self.pg_dsn = pg_dsn # PostgreSQL Data Source Name (DSN) for connecting to the database　thank you
        self.batch_size = max(1, int(batch_size))
        self.failures_only = bool(failures_only)
        self.worker_id = worker_id
        self._buffer: list[tuple[str, dict[str, Any]]] = []
        self._conn: Any = None
        self.dberror : Optional[Exception] = None

    def _connect(self):
        if self._conn is not None:
            # psycopg2 connection.closed == 0 means open; non-zero means closed.
            if getattr(self._conn, "closed", 1) == 0:
                return self._conn
            self._conn = None

        if not self.pg_dsn:
            raise ValueError("pg_dsn is required for PostgreSQL connection")
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed. Install psycopg2-binary.")
        self._conn = psycopg2.connect(self.pg_dsn)
        self._conn.autocommit = False
        with self._conn.cursor() as cursor:
            set_search_path(cursor, get_etl_schema(), get_public_schema())
        return self._conn

    @staticmethod
    def _close_cursor(cursor) -> None:
        if cursor is None:
            return
        try:
            cursor.close()
        except Exception:
            pass

    @staticmethod
    def _rollback_if_open(conn) -> None:
        if conn is None or getattr(conn, "closed", 1) != 0:
            return
        try:
            conn.rollback()
        except Exception:
            pass

    def _discard_connection(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass

    def close(self) -> None:
        self.flush()
        self._discard_connection()

    def init_db(self) -> None:
        with open(self.schema_path, "r", encoding="utf-8") as f:
            schema_sql = render_sql_template(f.read())
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(schema_sql)
            conn.commit()
        except Exception as e:
            self._rollback_if_open(conn)
            self.dberror = e
            raise
        finally:
            self._close_cursor(cur)

    def create_run(self, root_seed_count: int, notes: str = "") -> int:
        self.flush()
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                f"""
                INSERT INTO {CRAWL_RUNS_TABLE} (root_seed_count, notes)
                VALUES (%s, %s)
                RETURNING id
                """,
                (root_seed_count, notes),
            )
            run_id = int(cur.fetchone()[0])
            conn.commit()
            return run_id
        
        except Exception as e:
            self._rollback_if_open(conn)
            self.dberror = e
            raise

        finally:
            self._close_cursor(cur)

    def finish_run(self, run_id: int, status: str, notes: str = "") -> None:
        self.flush()
        for attempt in range(2):
            conn = None
            cur = None
            try:
                conn = self._connect()
                cur = conn.cursor()
                cur.execute(
                    f"""
                    UPDATE {CRAWL_RUNS_TABLE}
                    SET status = %s, finished_at = NOW(), notes = COALESCE(NULLIF(%s, ''), notes)
                        WHERE id = %s
                        """,
                    (status, notes, run_id),
                )
                conn.commit()
                return
            except Exception as exc:
                self._rollback_if_open(conn)
                self._discard_connection()
                self.dberror = exc
                if attempt == 0:
                    print(
                        f"[QUEUE_LOG][WARN] finish_run retry after {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    continue
                raise
            finally:
                self._close_cursor(cur)

    def start_worker_run(self, run_id: int, worker_id: str) -> None:
        table_ref = get_table_ref("CRAWL_WORKER_RUNS_TABLE")
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table_ref} (run_id, worker_id)
                    VALUES (%s, %s)
                    ON CONFLICT (run_id, worker_id) DO UPDATE
                    SET last_heartbeat_at = NOW(), finished_at = NULL, status = 'running'
                    """,
                    (run_id, worker_id),
                )
            conn.commit()
        except Exception:
            self._rollback_if_open(conn)
            raise

    def update_worker_run(
        self,
        run_id: int,
        worker_id: str,
        *,
        messages_received: int = 0,
        urls_completed: int = 0,
        urls_failed: int = 0,
        records_extracted: int = 0,
        status: str = "running",
    ) -> None:
        table_ref = get_table_ref("CRAWL_WORKER_RUNS_TABLE")
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {table_ref}
                    SET last_heartbeat_at = NOW(),
                        finished_at = CASE WHEN %s = 'running' THEN NULL ELSE NOW() END,
                        status = %s,
                        messages_received = messages_received + %s,
                        urls_completed = urls_completed + %s,
                        urls_failed = urls_failed + %s,
                        records_extracted = records_extracted + %s
                    WHERE run_id = %s AND worker_id = %s
                    """,
                    (
                        status,
                        status,
                        messages_received,
                        urls_completed,
                        urls_failed,
                        records_extracted,
                        run_id,
                        worker_id,
                    ),
                )
            conn.commit()
        except Exception:
            self._rollback_if_open(conn)
            raise

    def count_unfinished_tasks(self, run_id: int) -> int:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {CRAWL_QUEUE_STATE_TABLE}
                    WHERE run_id = %s AND status IN ('pending', 'processing')
                    """,
                    (run_id,),
                )
                return int(cur.fetchone()[0])
        except Exception:
            self._rollback_if_open(conn)
            raise

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        self._buffer.append((kind, payload))
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def upsert_queue_state(
        self,
        *,
        run_id: int,
        url: str,
        parent_url: str,
        domain: str,
        depth: int,
        status: str,
        discovered_from: str = "",
        retry_count: int = 0,
        fetch_method: Optional[str] = None,
        status_code: Optional[int] = None,
        last_error_type: str = "",
        last_error_message: str = "",
        set_started: bool = False,
        set_finished: bool = False,
    ) -> None:
        if self.failures_only and status != "error":
            return

        self._enqueue(
            "queue_state",
            {
                "run_id": run_id,
                "url": url,
                "parent_url": parent_url,
                "domain": domain,
                "depth": depth,
                "status": status,
                "discovered_from": discovered_from,
                "retry_count": retry_count,
                "fetch_method": fetch_method,
                "status_code": status_code,
                "last_error_type": last_error_type,
                "last_error_message": last_error_message,
                "set_started": set_started,
                "set_finished": set_finished,
                "worker_id": self.worker_id,
            },
        )

    def add_attempt(
        self,
        *,
        run_id: int,
        url: str,
        fetch_method: str,
        ok: bool,
        status_code: Optional[int],
        error_type: str,
        error_message: str,
        final_url: str,
        response_bytes: Optional[int],
        used_fallback: bool,
        connection_log: list[dict[str, Any]],
    ) -> None:
        if self.failures_only and ok:
            return

        self._enqueue(
            "attempt",
            {
                "run_id": run_id,
                "url": url,
                "fetch_method": fetch_method,
                "ok": ok,
                "status_code": status_code,
                "error_type": error_type,
                "error_message": error_message,
                "final_url": final_url,
                "response_bytes": response_bytes,
                "used_fallback": used_fallback,
                "connection_log": connection_log,
                "worker_id": self.worker_id,
            },
        )

    def add_edge(
        self,
        *,
        run_id: int,
        parent_url: str,
        child_url: str,
        parent_domain: str,
        child_domain: str,
        depth: int,
        source: str,
    ) -> None:
        if self.failures_only:
            return
        self._enqueue(
            "edge",
            {
                "run_id": run_id,
                "parent_url": parent_url,
                "child_url": child_url,
                "parent_domain": parent_domain,
                "child_domain": child_domain,
                "depth": depth,
                "source": source,
            },
        )

    def add_tag_keyword_hits(
        self,
        *,
        run_id: int,
        url: str,
        domain: str,
        tag_hits: list[dict[str, Any]],
    ) -> None:
        if not tag_hits:
            return
        for item in tag_hits:
            hit_count = int(item.get("hit_count", 0) or 0)
            weight = int(item.get("weight", 0) or 0)
            weighted_score = int(item.get("weighted_score", 0) or 0)
            if hit_count <= 0 or weight < 0 or weighted_score < 0:
                continue
            self._enqueue(
                "tag_keyword_hit",
                {
                    "run_id": run_id,
                    "url": url,
                    "domain": domain,
                    "tag_name": str(item.get("tag_name", "")),
                    "course_type": str(item.get("course_type", "general")),
                    "keyword": str(item.get("keyword", "")),
                    "hit_count": hit_count,
                    "weight": weight,
                    "weighted_score": weighted_score,
                },
            )

    def add_tag_class_counts(
        self,
        *,
        run_id: int,
        url: str,
        domain: str,
        class_counts: list[dict[str, Any]],
    ) -> None:
        if not class_counts:
            return
        for item in class_counts:
            occurrence_count = int(item.get("occurrence_count", 0) or 0)
            if occurrence_count <= 0:
                continue
            self._enqueue(
                "tag_class_count",
                {
                    "run_id": run_id,
                    "url": url,
                    "domain": domain,
                    "tag_name": str(item.get("tag_name", "")),
                    "class_name": str(item.get("class_name", "")),
                    "occurrence_count": occurrence_count,
                },
            )

    def _write_queue_state(self, cur, p: dict[str, Any]) -> None:
        cur.execute(
            f"""
            INSERT INTO {CRAWL_QUEUE_STATE_TABLE} (
                run_id, url, parent_url, domain, depth, status,
                fetch_method, retry_count, discovered_from,
                status_code, last_error_type, last_error_message,
                worker_id, started_at, finished_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, CASE WHEN %s THEN NOW() ELSE NULL END,
                CASE WHEN %s THEN NOW() ELSE NULL END,
                NOW()
            )
            ON CONFLICT(run_id, url) DO UPDATE SET
                parent_url = EXCLUDED.parent_url,
                domain = EXCLUDED.domain,
                depth = EXCLUDED.depth,
                status = EXCLUDED.status,
                fetch_method = COALESCE(EXCLUDED.fetch_method, {CRAWL_QUEUE_STATE_TABLE}.fetch_method),
                retry_count = EXCLUDED.retry_count,
                discovered_from = EXCLUDED.discovered_from,
                status_code = EXCLUDED.status_code,
                last_error_type = EXCLUDED.last_error_type,
                last_error_message = EXCLUDED.last_error_message,
                worker_id = COALESCE(NULLIF(EXCLUDED.worker_id, ''), {CRAWL_QUEUE_STATE_TABLE}.worker_id),
                started_at = COALESCE(EXCLUDED.started_at, {CRAWL_QUEUE_STATE_TABLE}.started_at),
                finished_at = COALESCE(EXCLUDED.finished_at, {CRAWL_QUEUE_STATE_TABLE}.finished_at),
                updated_at = NOW()
            """,
            (
                p["run_id"],
                p["url"],
                p["parent_url"],
                p["domain"],
                p["depth"],
                p["status"],
                p["fetch_method"],
                p["retry_count"],
                p["discovered_from"],
                p["status_code"],
                p["last_error_type"],
                p["last_error_message"],
                p["worker_id"],
                bool(p["set_started"]),
                bool(p["set_finished"]),
            ),
        )
        return

    def _write_attempt(self, cur, p: dict[str, Any]) -> None:
        if self.failures_only:
            cur.execute(
                f"""
                INSERT INTO {CRAWL_FAILURES_TABLE} (
                    run_id, url, domain, fetch_method, status_code, error_type, error_message, connection_log, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    p["run_id"],
                    p["url"],
                    self._extract_domain(p["url"]),
                    p["fetch_method"],
                    p["status_code"],
                    p["error_type"],
                    p["error_message"],
                    json.dumps(p["connection_log"], ensure_ascii=False),
                ),
            )
            return

        cur.execute(
            f"SELECT id FROM {CRAWL_QUEUE_STATE_TABLE} WHERE run_id = %s AND url = %s",
            (p["run_id"], p["url"]),
        )
        row = cur.fetchone()
        if row is None:
            return
        queue_state_id = int(row[0])


        cur.execute(
            f"SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM {CRAWL_ATTEMPTS_TABLE} WHERE queue_state_id = %s",
            (queue_state_id,),
        )
        attempt_no = int(cur.fetchone()[0])


        cur.execute(
            f"""
            INSERT INTO {CRAWL_ATTEMPTS_TABLE} (
                queue_state_id, attempt_no, fetch_method, ok, status_code,
                error_type, error_message, final_url, response_bytes,
                used_fallback, connection_log, worker_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                queue_state_id,
                attempt_no,
                p["fetch_method"],
                int(bool(p["ok"])),
                p["status_code"],
                p["error_type"],
                p["error_message"],
                p["final_url"],
                p["response_bytes"],
                int(bool(p["used_fallback"])),
                json.dumps(p["connection_log"], ensure_ascii=False),
                p["worker_id"],
            ),
        )
        return

    def _write_edge(self, cur, p: dict[str, Any]) -> None:
        cur.execute(
            f"""
            INSERT INTO {CRAWL_EDGES_TABLE} (
                run_id, parent_url, child_url, parent_domain, child_domain, depth, source
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(run_id, parent_url, child_url, source) DO NOTHING
            """,
            (
                p["run_id"],
                p["parent_url"],
                p["child_url"],
                p["parent_domain"],
                p["child_domain"],
                p["depth"],
                p["source"],
            ),
        )
        return

    def _write_tag_keyword_hit(self, cur, p: dict[str, Any]) -> None:
        cur.execute(
            f"""
            INSERT INTO {CRAWL_TAG_KEYWORD_HITS_TABLE} (
                run_id, domain, url, tag_name, course_type, keyword,
                hit_count, weight, weighted_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                p["run_id"],
                p["domain"],
                p["url"],
                p["tag_name"],
                p["course_type"],
                p["keyword"],
                p["hit_count"],
                p["weight"],
                p["weighted_score"],
            ),
        )

        cur.execute(
            f"""
            INSERT INTO {CRAWL_DOMAIN_TAG_SCORES_TABLE} (
                run_id, domain, tag_name, course_type, keyword,
                total_hits, total_weighted_score, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT(run_id, domain, tag_name, course_type, keyword)
            DO UPDATE SET
                total_hits = {CRAWL_DOMAIN_TAG_SCORES_TABLE}.total_hits + EXCLUDED.total_hits,
                total_weighted_score = {CRAWL_DOMAIN_TAG_SCORES_TABLE}.total_weighted_score + EXCLUDED.total_weighted_score,
                updated_at = NOW()
            """,
            (
                p["run_id"],
                p["domain"],
                p["tag_name"],
                p["course_type"],
                p["keyword"],
                p["hit_count"],
                p["weighted_score"],
            ),
        )
        return

    def _write_tag_class_count(self, cur, p: dict[str, Any]) -> None:
        cur.execute(
            f"""
            INSERT INTO {CRAWL_TAG_CLASS_COUNTS_TABLE} (
                run_id, domain, url, tag_name, class_name, occurrence_count
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                p["run_id"],
                p["domain"],
                p["url"],
                p["tag_name"],
                p["class_name"],
                p["occurrence_count"],
            ),
        )

        cur.execute(
            f"""
            INSERT INTO {CRAWL_DOMAIN_CLASS_COUNTS_TABLE} (
                run_id, domain, tag_name, class_name, total_occurrences, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT(run_id, domain, tag_name, class_name)
            DO UPDATE SET
                total_occurrences = {CRAWL_DOMAIN_CLASS_COUNTS_TABLE}.total_occurrences + EXCLUDED.total_occurrences,
                updated_at = NOW()
            """,
            (
                p["run_id"],
                p["domain"],
                p["tag_name"],
                p["class_name"],
                p["occurrence_count"],
            ),
        )
        return

    @staticmethod
    def _count_by_kind(items: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
        counts = {"queue_state": 0, "attempt": 0, "edge": 0, "tag_keyword_hit": 0, "tag_class_count": 0}
        for kind, _ in items:
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def flush(self) -> None:
        if not self._buffer:
            return
        conn = self._connect()
        cur = conn.cursor()
        started = time.perf_counter()
        items = self._buffer
        event_count = len(items)
        counts = self._count_by_kind(items)
        try:
            self._buffer = []
            for kind, payload in items:
                if kind == "queue_state":
                    self._write_queue_state(cur, payload)
                elif kind == "attempt":
                    self._write_attempt(cur, payload)
                elif kind == "edge":
                    self._write_edge(cur, payload)
                elif kind == "tag_keyword_hit":
                    self._write_tag_keyword_hit(cur, payload)
                elif kind == "tag_class_count":
                    self._write_tag_class_count(cur, payload)
            conn.commit()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            ms_per_row = elapsed_ms / event_count if event_count else 0.0
            print(
                "[QUEUE_LOG] flush ok "
                f"events={event_count} "
                f"queue_state={counts.get('queue_state', 0)} "
                f"attempt={counts.get('attempt', 0)} "
                f"edge={counts.get('edge', 0)} "
                f"tag_keyword_hit={counts.get('tag_keyword_hit', 0)} "
                f"tag_class_count={counts.get('tag_class_count', 0)} "
                f"elapsed_ms={elapsed_ms:.3f} "
                f"ms_per_row={ms_per_row:.6f}",
                flush=True,
            )
        except Exception as e:
            self._rollback_if_open(conn)
            self._discard_connection()
            self._buffer = items + self._buffer
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            print(
                "[QUEUE_LOG] flush error "
                f"events={event_count} "
                f"queue_state={counts.get('queue_state', 0)} "
                f"attempt={counts.get('attempt', 0)} "
                f"edge={counts.get('edge', 0)} "
                f"tag_keyword_hit={counts.get('tag_keyword_hit', 0)} "
                f"tag_class_count={counts.get('tag_class_count', 0)} "
                f"elapsed_ms={elapsed_ms:.3f} "
                f"error_type={type(e).__name__} "
                f"error={e}",
                flush=True,
            )
            raise
        finally:
            self._close_cursor(cur)

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return url.split("//", 1)[1].split("/", 1)[0]
        except Exception:
            return ""

