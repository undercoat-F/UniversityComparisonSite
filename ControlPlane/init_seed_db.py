import os
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

from db.schema_config import get_observer_schema, get_public_schema, get_table_ref, render_sql_template, set_search_path

load_dotenv(encoding="utf-8-sig")

SCHEMA_PATH = os.path.join("ControlPlane", "seed_urls_schema_pg.sql")
SEED_URLS_TABLE = get_table_ref("SEED_URLS_TABLE")


def infer_country(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.endswith(".ac.uk") or host.endswith(".gov.uk") or host.endswith(".uk"):
        return "UK"
    if host.endswith(".edu") or host.endswith(".gov"):
        return "US"
    if host.endswith(".ac.jp") or host.endswith(".go.jp") or host.endswith(".jp"):
        return "JP"
    if host.endswith(".au"):
        return "AU"
    if host.endswith(".nz"):
        return "NZ"
    if host.endswith(".ie"):
        return "IE"
    if host.endswith(".ca"):
        return "CA"
    return "unknown"


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


def init_db(schema_path=SCHEMA_PATH):
    """PostgreSQL に seed_urls テーブルを作成"""
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"schema file not found: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = render_sql_template(f.read())

    db_params = get_db_params()
    try:
        with psycopg2.connect(**db_params) as conn:
            cursor = conn.cursor()
            set_search_path(cursor, get_observer_schema(), get_public_schema())
            cursor.execute(schema_sql)
            conn.commit()
            print(f"seed_urls テーブルを作成/確認しました")
    except psycopg2.Error as e:
        print(f"DB初期化エラー: {e}")
        raise


def upsert_targets(targets, db_path=None):
    """targets をupsert してseed_urls に投入
    
    Args:
        targets: [(root_url, depth), ...]
        db_path: 未使用（PostgreSQL 接続を使用）
    """
    rows = []
    for root_url, depth in targets:
        domain = urlparse(root_url).netloc
        country = infer_country(root_url)
        rows.append((country, domain, root_url, depth))

    db_params = get_db_params()
    try:
        with psycopg2.connect(**db_params) as conn:
            cursor = conn.cursor()
            set_search_path(cursor, get_observer_schema(), get_public_schema())
            for country, domain, root_url, depth in rows:
                cursor.execute(
                    f"""
                    INSERT INTO {SEED_URLS_TABLE} (country, domain, root_url, depth)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(domain, root_url) DO UPDATE SET
                        country=EXCLUDED.country,
                        depth=EXCLUDED.depth,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (country, domain, root_url, depth)
                )
            conn.commit()
            print(f"挿入/更新: {len(rows)} 件")
    except psycopg2.Error as e:
        print(f"upsert エラー: {e}")
        raise


def count_enabled(db_path=None):
    """enabled = 1 のレコード数をカウント"""
    db_params = get_db_params()
    try:
        with psycopg2.connect(**db_params) as conn:
            cursor = conn.cursor()
            set_search_path(cursor, get_observer_schema(), get_public_schema())
            cursor.execute(f"SELECT COUNT(*) FROM {SEED_URLS_TABLE} WHERE enabled = 1")
            return cursor.fetchone()[0]
    except psycopg2.Error as e:
        print(f"count_enabled エラー: {e}")
        return 0


def main():
    init_db()
    # targets の読み込みはここでは未実装（必要に応じて追加）
    enabled_count = count_enabled()
    print(f"DB準備完了: PostgreSQL seed_urls テーブル")
    print(f"enabled seed urls: {enabled_count}")


if __name__ == "__main__":
    main()


