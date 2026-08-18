#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CSVファイルをPostgreSQLに投入するスクリプト（関数型）
スキーマ作成 -> CSV読み込み -> 正規形でINSERT/UPDATE
"""

import os

import psycopg2
from psycopg2 import Error, sql
from psycopg2.extras import execute_values
from dotenv import load_dotenv

from db.schema_config import get_table_ref

load_dotenv(encoding="utf-8-sig")

BATCH_SIZE_ENV = "DB_SAVER_BATCH_SIZE"
BATCH_SIZE_DEFAULT = 100
UNIVERSITIES_TABLE = get_table_ref("UNIVERSITIES_TABLE")
DEGREE_PROGRAMS_TABLE = get_table_ref("DEGREE_PROGRAMS_TABLE")
TUITION_PATTERNS_TABLE = get_table_ref("TUITION_PATTERNS_TABLE")
PROGRAM_TUITION_MAP_TABLE = get_table_ref("PROGRAM_TUITION_MAP_TABLE")


def require_env(keys):
    """必須環境変数の存在を検証する。"""
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f".envに必要な環境変数が設定されていません: {missing}")


def get_db_params_from_env():
    """DB接続パラメータを.envから取得する。"""
    require_env(["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"])
    return {
        "host": os.getenv("DB_HOST"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": int(os.getenv("DB_PORT")),
    }


def connect(db_params):
    """PostgreSQLに接続する。"""
    enc_candidates = [None, "UTF8", "SJIS"]

    for enc in enc_candidates:
        try:
            if enc:
                os.environ["PGCLIENTENCODING"] = enc
            conn = psycopg2.connect(**db_params)
            print(f"PostgreSQL接続成功: {db_params['dbname']}")
            return conn
        except UnicodeDecodeError as e:
            print(f"接続時デコードエラー（encoding={enc or 'default'}）: {e}")
            continue
        except Error as e:
            print(f"接続エラー: {e}")
            return None
        except Exception as e:
            print(f"予期しない接続エラー: {e}")
            return None

    print("接続失敗: 文字コード設定を切り替えても接続できませんでした。")
    return None


def ensure_database_exists(db_params):
    """接続先DBが無ければ作成する（管理DB: postgres に接続）。"""
    admin_params = dict(db_params)
    admin_params["dbname"] = "postgres"
    target_db = db_params["dbname"]

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**admin_params)
        conn.autocommit = True
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
        exists = cursor.fetchone() is not None

        if exists:
            print(f"DB確認: {target_db} は既に存在します")
        else:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
            print(f"DB作成: {target_db} を新規作成しました")

        return True
    except Error as e:
        print(f"DB存在確認/作成エラー: {e}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def disconnect(conn):
    """接続を切断する。"""
    if conn:
        conn.close()
        print("接続を閉じました")


def create_schema(conn):
    """正規形スキーマを作成し、重複制御に必要な制約/インデックスを準備する。"""
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {UNIVERSITIES_TABLE} (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                country VARCHAR(100),
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DEGREE_PROGRAMS_TABLE} (
                id SERIAL PRIMARY KEY,
                university_id INTEGER NOT NULL REFERENCES {UNIVERSITIES_TABLE}(id) ON DELETE CASCADE,
                program_name VARCHAR(500) NOT NULL,
                course_type VARCHAR(100),
                is_online BOOLEAN DEFAULT FALSE,
                source_url TEXT,
                last_seen TIMESTAMP,
                quality_flag VARCHAR(50) DEFAULT 'high',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TUITION_PATTERNS_TABLE} (
                id SERIAL PRIMARY KEY,
                degree_level VARCHAR(50),
                amount DECIMAL(10, 2),
                currency VARCHAR(10),
                fee_type VARCHAR(50) DEFAULT 'tuition',
                tuition_type VARCHAR(30) DEFAULT 'unknown',
                amount_min DECIMAL(10, 2),
                amount_max DECIMAL(10, 2),
                normalized_monthly_amount DECIMAL(10, 2),
                normalization_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (degree_level, amount, currency, fee_type, tuition_type)
            );
            """
        )

        # 既存DBを段階移行できるよう、新列を追加してから制約を置き換える。
        cursor.execute(f"ALTER TABLE {TUITION_PATTERNS_TABLE} ADD COLUMN IF NOT EXISTS tuition_type VARCHAR(30) DEFAULT 'unknown';")
        cursor.execute(f"ALTER TABLE {TUITION_PATTERNS_TABLE} ADD COLUMN IF NOT EXISTS amount_min DECIMAL(10, 2);")
        cursor.execute(f"ALTER TABLE {TUITION_PATTERNS_TABLE} ADD COLUMN IF NOT EXISTS amount_max DECIMAL(10, 2);")
        cursor.execute(f"ALTER TABLE {TUITION_PATTERNS_TABLE} ADD COLUMN IF NOT EXISTS normalized_monthly_amount DECIMAL(10, 2);")
        cursor.execute(f"ALTER TABLE {TUITION_PATTERNS_TABLE} ADD COLUMN IF NOT EXISTS normalization_note TEXT;")
        cursor.execute(f"UPDATE {TUITION_PATTERNS_TABLE} SET tuition_type = 'unknown' WHERE tuition_type IS NULL;")
        cursor.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name='tuition_patterns'
                      AND constraint_name='tuition_patterns_degree_level_amount_currency_fee_type_key'
                ) THEN
                    ALTER TABLE {TUITION_PATTERNS_TABLE}
                    DROP CONSTRAINT tuition_patterns_degree_level_amount_currency_fee_type_key;
                END IF;
            END $$;
            """
        )
        cursor.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tuition_patterns_v2
            ON {TUITION_PATTERNS_TABLE} (degree_level, amount, currency, fee_type, tuition_type);
            """
        )

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PROGRAM_TUITION_MAP_TABLE} (
                degree_program_id INTEGER NOT NULL REFERENCES {DEGREE_PROGRAMS_TABLE}(id) ON DELETE CASCADE,
                tuition_pattern_id INTEGER NOT NULL REFERENCES {TUITION_PATTERNS_TABLE}(id) ON DELETE CASCADE,
                PRIMARY KEY (degree_program_id, tuition_pattern_id)
            );
            """
        )

        # 既存データと今後のUPSERT整合性を取るため、NULLを埋める。
        cursor.execute(f"UPDATE {DEGREE_PROGRAMS_TABLE} SET source_url = '' WHERE source_url IS NULL;")
        cursor.execute(f"UPDATE {DEGREE_PROGRAMS_TABLE} SET course_type = '' WHERE course_type IS NULL;")
        cursor.execute(f"UPDATE {DEGREE_PROGRAMS_TABLE} SET is_online = FALSE WHERE is_online IS NULL;")

        # 既存データに重複があるとUNIQUE INDEX作成に失敗するため先に正規化する。
        cursor.execute(
            f"""
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY university_id, source_url, program_name, course_type, is_online
                        ORDER BY id
                    ) AS rn
                FROM {DEGREE_PROGRAMS_TABLE}
            )
            DELETE FROM {DEGREE_PROGRAMS_TABLE} d
            USING ranked r
            WHERE d.id = r.id AND r.rn > 1;
            """
        )

        cursor.execute(f"ALTER TABLE {DEGREE_PROGRAMS_TABLE} ALTER COLUMN source_url SET DEFAULT '';")
        cursor.execute(f"ALTER TABLE {DEGREE_PROGRAMS_TABLE} ALTER COLUMN course_type SET DEFAULT '';")
        cursor.execute(f"ALTER TABLE {DEGREE_PROGRAMS_TABLE} ALTER COLUMN is_online SET DEFAULT FALSE;")

        # URL再探索時の重複制御に使う自然キー。
        cursor.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_degree_program_natural_idx
            ON {DEGREE_PROGRAMS_TABLE} (university_id, source_url, program_name, course_type, is_online);
            """
        )

        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_uni_name ON {UNIVERSITIES_TABLE}(name);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_prog_uni_id ON {DEGREE_PROGRAMS_TABLE}(university_id);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_map_prog_id ON {PROGRAM_TUITION_MAP_TABLE}(degree_program_id);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_map_pattern_id ON {PROGRAM_TUITION_MAP_TABLE}(tuition_pattern_id);")
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_pattern_level ON {TUITION_PATTERNS_TABLE}(degree_level);")

        conn.commit()
        print("スキーマ作成/更新完了")
        return True
    except Error as e:
        print(f"スキーマ作成エラー: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()

#正規化　判定エラー防止用のstrip,lower 
def parse_bool(raw):
    return str(raw).strip().lower() in ("1", "true", "t", "yes", "y")


def parse_optional_float(raw):
    text = str(raw).strip()
    if not text:
        return None

    normalized = text.lower()
    if normalized in ("none", "null", "nan", "n/a", "na", "-"):
        return None

    return float(text)


def parse_optional_timestamp(raw):
    text = str(raw).strip()
    if not text:
        return None
    if text.lower() in ("none", "null", "nan"):
        return None
    return text


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _sync_id_sequences(conn):
    """SERIAL シーケンスを各テーブルの最大 id に同期する。"""
    cursor = conn.cursor()
    try:
        targets = (UNIVERSITIES_TABLE, DEGREE_PROGRAMS_TABLE, TUITION_PATTERNS_TABLE)
        for table_ref in targets:
            cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table_ref,))
            row = cursor.fetchone()
            seq_name = row[0] if row else None
            if not seq_name:
                continue
            cursor.execute(
                f"SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {table_ref}), 1), true)",
                (seq_name,),
            )
        conn.commit()
    except Error:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _insert_universities_rows(conn, rows):
    cursor = conn.cursor()
    inserted = 0
    id_map = {}
    try:
        payload = []
        for row in rows:
            csv_id = str(row.get("id", "")).strip()
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            country = str(row.get("country", "")).strip() or None
            url = str(row.get("url", "")).strip() or None
            created_at = parse_optional_timestamp(row.get("created_at", ""))
            payload.append((csv_id, name, country, url, created_at))

        batch_size = max(1, _env_int(BATCH_SIZE_ENV, BATCH_SIZE_DEFAULT))
        sql_stmt = f"""
            WITH src(csv_id, name, country, url, created_at) AS (VALUES %s),
            upsert AS (
            INSERT INTO {UNIVERSITIES_TABLE} (name, country, url, created_at, updated_at)
                SELECT
                    src.name,
                    src.country,
                    src.url,
                    COALESCE(src.created_at::timestamp, NOW()),
                    NOW()
                FROM src
                ON CONFLICT (name) DO UPDATE SET
                    country = EXCLUDED.country,
                    url = EXCLUDED.url,
                    updated_at = NOW()
                RETURNING id, name
            )
            SELECT src.csv_id, upsert.id
            FROM src
            JOIN upsert ON upsert.name = src.name;
        """
        for chunk in _chunked(payload, batch_size):
            execute_values(cursor, sql_stmt, chunk)
            for csv_id, db_id in cursor.fetchall():
                id_map[str(csv_id)] = int(db_id)
            inserted += len(chunk)

        conn.commit()
        return inserted, id_map
    except Error:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _insert_programs_rows(conn, rows, university_id_map):
    cursor = conn.cursor()
    inserted = 0
    skipped = 0
    id_map = {}
    try:
        payload = []
        csv_ids_by_natural_key = {}
        csv_ids_by_representative_id = {}
        for row in rows:
            csv_id = str(row.get("id", "")).strip()
            csv_uni_id = str(row.get("university_id", "")).strip()
            db_uni_id = university_id_map.get(csv_uni_id)
            if db_uni_id is None:
                skipped += 1
                continue

            program_name = str(row.get("program_name", "")).strip()
            if not program_name:
                skipped += 1
                continue

            course_type = str(row.get("course_type", "")).strip()
            is_online = parse_bool(row.get("is_online", "0"))
            source_url = str(row.get("source_url", "")).strip()
            last_seen = parse_optional_timestamp(row.get("last_seen", ""))
            quality_flag = str(row.get("quality_flag", "high")).strip() or "high"
            natural_key = (
                int(db_uni_id),
                source_url,
                program_name,
                course_type,
                bool(is_online),
            )
            if natural_key in csv_ids_by_natural_key:
                csv_ids_by_natural_key[natural_key].append(csv_id)
                continue

            csv_ids_by_natural_key[natural_key] = [csv_id]
            csv_ids_by_representative_id[csv_id] = csv_ids_by_natural_key[natural_key]
            payload.append(
                (
                    csv_id,
                    int(db_uni_id),
                    program_name,
                    course_type,
                    bool(is_online),
                    source_url,
                    last_seen,
                    quality_flag,
                )
            )

        batch_size = max(1, _env_int(BATCH_SIZE_ENV, BATCH_SIZE_DEFAULT))
        sql_stmt = f"""
            WITH src(
                csv_id,
                university_id,
                program_name,
                course_type,
                is_online,
                source_url,
                last_seen,
                quality_flag
            ) AS (VALUES %s),
            upsert AS (
                INSERT INTO {DEGREE_PROGRAMS_TABLE}
                (university_id, program_name, course_type, is_online, source_url, last_seen, quality_flag)
                SELECT
                    src.university_id,
                    src.program_name,
                    src.course_type,
                    src.is_online,
                    src.source_url,
                    src.last_seen::timestamp,
                    src.quality_flag
                FROM src
                ON CONFLICT (university_id, source_url, program_name, course_type, is_online)
                DO UPDATE SET
                    last_seen = EXCLUDED.last_seen,
                    quality_flag = EXCLUDED.quality_flag
                RETURNING id, university_id, source_url, program_name, course_type, is_online
            )
            SELECT src.csv_id, upsert.id
            FROM src
            JOIN upsert
                ON upsert.university_id = src.university_id
               AND upsert.source_url = src.source_url
               AND upsert.program_name = src.program_name
               AND upsert.course_type = src.course_type
               AND upsert.is_online = src.is_online;
        """
        for chunk in _chunked(payload, batch_size):
            execute_values(cursor, sql_stmt, chunk)
            for csv_id, db_id in cursor.fetchall():
                csv_id = str(csv_id)
                for original_csv_id in csv_ids_by_representative_id[csv_id]:
                    id_map[original_csv_id] = int(db_id)
            inserted += len(chunk)

        conn.commit()
        return inserted, skipped, id_map
    except Error:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _insert_patterns_rows(conn, rows):
    cursor = conn.cursor()
    inserted = 0
    id_map = {}
    try:
        payload = []
        for row in rows:
            csv_id = str(row.get("id", "")).strip()
            degree_level = str(row.get("degree_level", "")).strip() or None
            amount = parse_optional_float(row.get("amount", ""))
            currency = str(row.get("currency", "")).strip() or None
            fee_type = str(row.get("fee_type", "tuition")).strip() or "tuition"
            tuition_type = str(row.get("tuition_type", "unknown")).strip() or "unknown"
            amount_min = parse_optional_float(row.get("amount_min", ""))
            amount_max = parse_optional_float(row.get("amount_max", ""))
            normalized_monthly_amount = parse_optional_float(row.get("normalized_monthly_amount", ""))
            normalization_note = str(row.get("normalization_note", "unknown_not_normalized")).strip() or "unknown_not_normalized"
            payload.append(
                (
                    csv_id,
                    degree_level,
                    amount,
                    currency,
                    fee_type,
                    tuition_type,
                    amount_min,
                    amount_max,
                    normalized_monthly_amount,
                    normalization_note,
                )
            )

        batch_size = max(1, _env_int(BATCH_SIZE_ENV, BATCH_SIZE_DEFAULT))
        sql_stmt = f"""
            WITH src(
                csv_id,
                degree_level,
                amount,
                currency,
                fee_type,
                tuition_type,
                amount_min,
                amount_max,
                normalized_monthly_amount,
                normalization_note
            ) AS (VALUES %s),
            upsert AS (
                INSERT INTO {TUITION_PATTERNS_TABLE}
                (degree_level, amount, currency, fee_type, tuition_type, amount_min, amount_max, normalized_monthly_amount, normalization_note)
                SELECT
                    src.degree_level,
                    src.amount,
                    src.currency,
                    src.fee_type,
                    src.tuition_type,
                    src.amount_min,
                    src.amount_max,
                    src.normalized_monthly_amount,
                    src.normalization_note
                FROM src
                ON CONFLICT (degree_level, amount, currency, fee_type, tuition_type)
                DO UPDATE SET
                    amount_min = EXCLUDED.amount_min,
                    amount_max = EXCLUDED.amount_max,
                    normalized_monthly_amount = EXCLUDED.normalized_monthly_amount,
                    normalization_note = EXCLUDED.normalization_note
                RETURNING id, degree_level, amount, currency, fee_type, tuition_type
            )
            SELECT src.csv_id, upsert.id
            FROM src
            JOIN upsert
                ON upsert.degree_level IS NOT DISTINCT FROM src.degree_level
               AND upsert.amount IS NOT DISTINCT FROM src.amount
               AND upsert.currency IS NOT DISTINCT FROM src.currency
               AND upsert.fee_type = src.fee_type
               AND upsert.tuition_type = src.tuition_type;
        """
        for chunk in _chunked(payload, batch_size):
            execute_values(cursor, sql_stmt, chunk)
            for csv_id, db_id in cursor.fetchall():
                id_map[str(csv_id)] = int(db_id)
            inserted += len(chunk)

        conn.commit()
        return inserted, id_map
    except Error:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _insert_map_rows(conn, rows, program_id_map, pattern_id_map):
    cursor = conn.cursor()
    inserted = 0
    skipped = 0
    try:
        payload = []
        for row in rows:
            csv_program_id = str(row.get("degree_program_id", "")).strip()
            csv_pattern_id = str(row.get("tuition_pattern_id", "")).strip()
            db_program_id = program_id_map.get(csv_program_id)
            db_pattern_id = pattern_id_map.get(csv_pattern_id)
            if db_program_id is None or db_pattern_id is None:
                skipped += 1
                continue
            payload.append((int(db_program_id), int(db_pattern_id)))

        batch_size = max(1, _env_int(BATCH_SIZE_ENV, BATCH_SIZE_DEFAULT))
        sql_stmt = f"""
            INSERT INTO {PROGRAM_TUITION_MAP_TABLE} (degree_program_id, tuition_pattern_id)
            VALUES %s
            ON CONFLICT (degree_program_id, tuition_pattern_id) DO NOTHING;
        """
        for chunk in _chunked(payload, batch_size):
            execute_values(cursor, sql_stmt, chunk)
            inserted += len(chunk)

        conn.commit()
        return inserted, skipped
    except Error:
        conn.rollback()
        raise
    finally:
        cursor.close()


def load_all_from_rows(row_bundle):
    """メモリ上の正規化済み行データを直接DBへ投入する。"""
    db_params = get_db_params_from_env()
    if not ensure_database_exists(db_params):
        return False

    conn = connect(db_params)
    if not conn:
        return False

    try:
        if not create_schema(conn):
            return False

        uni_rows = row_bundle.get("universities", [])
        prog_rows = row_bundle.get("degree_programs", [])
        pattern_rows = row_bundle.get("tuition_patterns", [])
        map_rows = row_bundle.get("program_tuition_map", [])

        uni_count, uni_id_map = _insert_universities_rows(conn, uni_rows)
        prog_count, prog_skipped, prog_id_map = _insert_programs_rows(conn, prog_rows, uni_id_map)
        pattern_count, pattern_id_map = _insert_patterns_rows(conn, pattern_rows)
        map_count, map_skipped = _insert_map_rows(conn, map_rows, prog_id_map, pattern_id_map)

        print("\n" + "=" * 50)
        print("DB投入完了（direct mode）")
        print(f"  - universities: {uni_count}件")
        print(f"  - degree_programs: {prog_count}件 (skip={prog_skipped})")
        print(f"  - tuition_patterns: {pattern_count}件")
        print(f"  - program_tuition_map: {map_count}件 (skip={map_skipped})")
        print("=" * 50)
        return True
    finally:
        disconnect(conn)


def open_load_session():
    """直投入用のDBセッションを初期化して返す。"""
    db_params = get_db_params_from_env()
    if not ensure_database_exists(db_params):
        return None

    conn = connect(db_params)
    if not conn:
        return None

    if not create_schema(conn):
        disconnect(conn)
        return None

    _sync_id_sequences(conn)
    return conn


def close_load_session(conn):
    disconnect(conn)


def load_rows_chunk(conn, row_bundle):
    """既存セッションに対して1チャンク分を投入し、件数サマリを返す。"""
    uni_rows = row_bundle.get("universities", [])
    prog_rows = row_bundle.get("degree_programs", [])
    pattern_rows = row_bundle.get("tuition_patterns", [])
    map_rows = row_bundle.get("program_tuition_map", [])

    try:
        uni_count, uni_id_map = _insert_universities_rows(conn, uni_rows)
    except Error as exc:
        # 過去の同期処理等でシーケンスが巻き戻っている場合のみ、自動復旧して1回再試行する。
        if "universities_pkey" not in str(exc):
            raise
        _sync_id_sequences(conn)
        uni_count, uni_id_map = _insert_universities_rows(conn, uni_rows)

    prog_count, prog_skipped, prog_id_map = _insert_programs_rows(conn, prog_rows, uni_id_map)
    pattern_count, pattern_id_map = _insert_patterns_rows(conn, pattern_rows)
    map_count, map_skipped = _insert_map_rows(conn, map_rows, prog_id_map, pattern_id_map)

    return {
        "universities": uni_count,
        "degree_programs": prog_count,
        "degree_programs_skipped": prog_skipped,
        "tuition_patterns": pattern_count,
        "program_tuition_map": map_count,
        "program_tuition_map_skipped": map_skipped,
    }



