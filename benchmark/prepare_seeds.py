"""計測用の seed テーブルを bench-base ブランチに作り、Mock の起点 URL を登録する。

- テーブルは本番の observer.seed_urls とは別名（既定 observer.bench_seed_urls）にし、本番の seed には触れない
- 計測時は SEED_URLS_TABLE をこのテーブルに向ける（Legacy・分散版とも同じ SELECT で読む）
- depth は Legacy の探索深さ上限になるため、分散版の ETL_WORKER_MAX_DEPTH（既定 5）と揃える
- bench-run は bench-base の子なので、登録後に `python -m benchmark.neon_branch reset` を実行すると反映される

接続には本番と同じ DB_NAME / DB_USER / DB_PASSWORD / DB_PORT を使い、ホストだけ --db-host で指定する。
本番の DB_HOST と同じエンドポイントを指定した場合は何もせずに終了する。

使い方（リポジトリのルートで実行）:
  python -m benchmark.prepare_seeds --scenario benchmark/mock/scenarios/A_scaling.yaml --db-host <bench-base のホスト>
  python -m benchmark.prepare_seeds --seed-urls benchmark/results/A-inspect/seed_urls.txt --db-host <ホスト>
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_TABLE = "observer.bench_seed_urls"
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
COUNTRY = "BENCH"


def seed_urls_from_scenario(path: Path) -> list[str]:
    from benchmark.mock.scenario import load_scenario

    return load_scenario(path).seed_urls()


def seed_urls_from_file(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def endpoint_id(host: str) -> str:
    label = host.split(".", 1)[0]
    return label[: -len("-pooler")] if label.endswith("-pooler") else label


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the benchmark seed table on the bench-base branch")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario", help="scenario YAML (requires pyyaml)")
    source.add_argument("--seed-urls", help="seed_urls.txt written by inspect_scenario.py --out")
    parser.add_argument("--db-host", required=True, help="host of the bench-base branch (python -m benchmark.neon_branch status)")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--depth", type=int, default=5, help="max crawl depth; match ETL_WORKER_MAX_DEPTH")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(encoding="utf-8-sig")
    except ImportError:
        pass
    import psycopg2

    if not TABLE_RE.match(args.table):
        sys.exit(f"invalid table name: {args.table} (expected schema.table)")
    production_host = os.getenv("DB_HOST", "").strip()
    if production_host and endpoint_id(production_host) == endpoint_id(args.db_host):
        sys.exit("refusing: --db-host is the production endpoint (DB_HOST); pass the bench-base host")

    urls = seed_urls_from_scenario(Path(args.scenario)) if args.scenario else seed_urls_from_file(Path(args.seed_urls))
    if not urls:
        sys.exit("no seed URLs")
    rows = [(COUNTRY, urlparse(u).netloc, u, args.depth) for u in urls]
    schema = args.table.split(".", 1)[0]

    conn = psycopg2.connect(
        host=args.db_host,
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )
    try:
        with conn, conn.cursor() as cur:
            # 列構成は ControlPlane/seed_urls_schema_pg.sql と同じ
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {args.table} (
                  id SERIAL PRIMARY KEY,
                  country VARCHAR(50) NOT NULL,
                  domain VARCHAR(255) NOT NULL,
                  root_url VARCHAR(2048) NOT NULL,
                  depth INTEGER NOT NULL CHECK(depth >= 0),
                  enabled SMALLINT NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(domain, root_url)
                )
            """)
            # シナリオを切り替えたときに前のシナリオの起点が残らないよう、毎回入れ替える
            cur.execute(f"DELETE FROM {args.table}")
            cur.executemany(
                f"INSERT INTO {args.table} (country, domain, root_url, depth, enabled) VALUES (%s, %s, %s, %s, 1)",
                rows,
            )
            # クローラー（ControlPlane/schedular.py の load_targets_from_db）と同じ SELECT で確認する
            cur.execute(f"SELECT root_url, depth FROM {args.table} WHERE enabled = 1 ORDER BY id")
            loaded = cur.fetchall()
    finally:
        conn.close()

    print(f"{args.table} on {args.db_host}: {len(loaded)} seeds (depth={args.depth})")
    for url, depth in loaded[:3]:
        print(f"  {url} depth={depth}")
    print(f"next: python -m benchmark.neon_branch reset   # bench-run に反映する")
    print(f"for the run, set SEED_URLS_TABLE={args.table} and ETL_RECENT_SKIP_MONTHS=0")


if __name__ == "__main__":
    main()
