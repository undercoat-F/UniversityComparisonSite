"""Worker が抽出したレコードを一定件数・一定時間ごとにまとめて DB へ書き込む。

- 件数が RECORD_DB_BATCH_SIZE に達したとき、RECORD_DB_FLUSH_INTERVAL_SEC が経過したとき、終了時に書き込む
- 書き込みは db_saver.load_rows_chunk（ON CONFLICT による UPSERT）を使うため、同じレコードを再投入しても壊れない
- 書き込み前に Worker が異常終了した分は失われる（SQS のメッセージは処理直後に削除済みのため再取得されない）
- スキーマ作成・ID シーケンス同期は Control Plane（producer）が実行開始時に1回だけ行う。
  Worker から行うと、他の Worker の書き込み中にシーケンスが巻き戻り主キーが衝突するため
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable, Optional

DEFAULT_BATCH_SIZE = 200
DEFAULT_FLUSH_INTERVAL_SEC = 30
DEFAULT_MAX_PENDING = 1000

# transform_records_to_rows が使う項目だけを保持し、raw_text やタグ集計でメモリを圧迫しないようにする
LOAD_RECORD_KEYS = ("url", "title", "timestamp", "country", "degrees")


def record_db_enabled() -> bool:
    raw = os.getenv("RECORD_DB_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def to_load_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in LOAD_RECORD_KEYS}


def sort_row_bundle(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """複数 Worker が同じ行を更新するときのデッドロックを避けるため、自然キー順に並べる。

    行どうしは transform_records_to_rows が振ったローカル id で参照し合うため、並べ替えても対応は崩れない。
    """
    def text(value: Any) -> str:
        return "" if value is None else str(value)

    return {
        "universities": sorted(bundle.get("universities", []), key=lambda r: text(r.get("name"))),
        "degree_programs": sorted(
            bundle.get("degree_programs", []),
            key=lambda r: (text(r.get("source_url")), text(r.get("program_name")),
                           text(r.get("course_type")), text(r.get("is_online"))),
        ),
        "tuition_patterns": sorted(
            bundle.get("tuition_patterns", []),
            key=lambda r: tuple(text(r.get(k)) for k in ("degree_level", "amount", "currency", "fee_type", "tuition_type")),
        ),
        "program_tuition_map": sorted(
            bundle.get("program_tuition_map", []),
            key=lambda r: (text(r.get("degree_program_id")), text(r.get("tuition_pattern_id"))),
        ),
    }


class DbRecordLoader:
    """同期処理。RecordBatchWriter から別スレッドで呼ばれる。接続は使い回し、失敗時は1回だけ再接続して再試行する。"""

    def __init__(self, connect: Optional[Callable[[], Any]] = None) -> None:
        # db_saver は import 時にテーブル名の環境変数を要求するため、ここで読み込む
        from db import db_saver
        from db.json_to_rows import transform_records_to_rows

        self._db_saver = db_saver
        self._transform = transform_records_to_rows
        if connect is None:
            db_params = db_saver.get_db_params_from_env()
            connect = lambda: db_saver.connect(db_params)  # noqa: E731
        self._connect = connect
        self._conn = None

    def load(self, records: list[dict[str, Any]]) -> dict[str, int]:
        bundle = sort_row_bundle(self._transform(records))
        for attempt in (1, 2):
            try:
                if self._conn is None or getattr(self._conn, "closed", 0):
                    self._conn = self._connect()
                    if self._conn is None:
                        raise ConnectionError("could not connect to the record database")
                return self._db_saver.load_rows_chunk(self._conn, bundle)
            except Exception:
                self._discard()
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _discard(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def close(self) -> None:
        self._discard()


class RecordBatchWriter:
    def __init__(
        self,
        loader,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_sec: float = DEFAULT_FLUSH_INTERVAL_SEC,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._loader = loader
        self.batch_size = max(1, batch_size)
        self.flush_interval_sec = flush_interval_sec
        self.max_pending = max(self.batch_size, max_pending)
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._last_failure_at: Optional[float] = None
        self.written = 0
        self.dropped = 0
        self.failed_flushes = 0

    @property
    def pending(self) -> int:
        return len(self._buffer)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def add(self, domain: str, record: dict[str, Any]) -> None:
        """SiteState.record_sink として使う。"""
        self._buffer.append(to_load_record(record))
        if len(self._buffer) >= self.batch_size:
            self._wake.set()
        if len(self._buffer) >= self.max_pending:
            if self._recently_failed():
                # DB 障害中は書き込みを繰り返さず、古いものから捨てて上限を守る
                self._drop_oldest(len(self._buffer) - self.max_pending + 1)
            else:
                await self.flush()  # 書き込みが追いつかない場合は、ここで待たせてメモリの増加を止める

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval_sec)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if not self._stopping:
                await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch, self._buffer = self._buffer, []
            started = time.perf_counter()
            try:
                summary = await asyncio.to_thread(self._loader.load, batch)
            except Exception as exc:  # noqa: BLE001
                self.failed_flushes += 1
                self._last_failure_at = time.monotonic()
                self._buffer = batch + self._buffer
                overflow = len(self._buffer) - self.max_pending
                if overflow > 0:
                    self._drop_oldest(overflow)
                print(
                    f"[RECORD_DB][WARN] flush failed records={len(batch)} pending={len(self._buffer)} "
                    f"dropped_total={self.dropped}: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                return
            self._last_failure_at = None
            self.written += len(batch)
            print(
                f"[RECORD_DB] flushed records={len(batch)} written_total={self.written} "
                f"elapsed={time.perf_counter() - started:.2f}s rows={summary}",
                flush=True,
            )

    async def close(self) -> None:
        # 書き込み中にキャンセルするとバッチを失うため、停止を指示して実行中の書き込みの完了を待つ
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.flush()
        if self._buffer:
            self._drop_oldest(len(self._buffer))
        await asyncio.to_thread(self._loader.close)
        print(
            f"[RECORD_DB] closed written={self.written} dropped={self.dropped} failed_flushes={self.failed_flushes}",
            flush=True,
        )

    def _recently_failed(self) -> bool:
        return (
            self._last_failure_at is not None
            and time.monotonic() - self._last_failure_at < self.flush_interval_sec
        )

    def _drop_oldest(self, count: int) -> None:
        if count <= 0:
            return
        del self._buffer[:count]
        self.dropped += count
        print(f"[RECORD_DB][ERROR] dropped {count} records (dropped_total={self.dropped})", flush=True)
