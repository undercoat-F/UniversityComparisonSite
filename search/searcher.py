#例外にするべきもの
# DB接続失敗
# SQL実行エラー
# 入力値不正
#欠損値はnullで返す
#必要レコードだけselectする
#このAPIの権限はselectだけでいい
#DBの権限はPostgreSQLのロールで決定されるため、DB_configに注意

import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from dotenv import load_dotenv

from db.schema_config import get_public_schema, get_table_ref, set_search_path

load_dotenv()

# 親DB（本番）の接続情報を優先。なければ開発DB使用
def get_db_config():
    """
    本番環境では PARENT_DB_HOST/USER/PASSWORD を使用
    開発環境では DB_HOST/READUSER/PASSWORD を使用
    """
    parent_host = os.getenv("PARENT_DB_HOST")
    
    if parent_host:
        # 本番環境（親DB）
        _REQUIRED_ENV = ["PARENT_DB_HOST", "PARENT_DB_NAME", "PARENT_DB_USER", "PARENT_DB_PASSWORD", "PARENT_DB_PORT"]
        _missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if _missing:
            raise EnvironmentError(f".envに必要な環境変数が設定されていません: {_missing}")
        
        return {
            "host": os.getenv("PARENT_DB_HOST"),
            "dbname": os.getenv("PARENT_DB_NAME"),
            "user": os.getenv("PARENT_DB_USER"),
            "password": os.getenv("PARENT_DB_PASSWORD"),
            "port": int(os.getenv("PARENT_DB_PORT")),
            "sslmode": os.getenv("DB_SSLMODE", "require"),
        }
    else:
        # 開発環境（子DB）
        _REQUIRED_ENV = ["DB_HOST", "DB_NAME", "DB_READUSER", "DB_READPASSWORD", "DB_PORT"]
        _missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
        if _missing:
            raise EnvironmentError(f".envに必要な環境変数が設定されていません: {_missing}")
        
        return {
            "host": os.getenv("DB_HOST"),
            "dbname": os.getenv("DB_NAME"),
            "user": os.getenv("DB_READUSER"),
            "password": os.getenv("DB_READPASSWORD"),
            "port": int(os.getenv("DB_PORT")),
            "sslmode": os.getenv("DB_SSLMODE", "require"),
        }

DB_CONFIG = get_db_config()
UNIVERSITIES_TABLE = get_table_ref("UNIVERSITIES_TABLE")
DEGREE_PROGRAMS_TABLE = get_table_ref("DEGREE_PROGRAMS_TABLE")
TUITION_PATTERNS_TABLE = get_table_ref("TUITION_PATTERNS_TABLE")
PROGRAM_TUITION_MAP_TABLE = get_table_ref("PROGRAM_TUITION_MAP_TABLE")

# 学位プログラムではないと判定した行（excluded_reason に理由あり）は返さない。
# needs_review は確認待ちの印で、本物のプログラムも含むため表示する（db/cleanup/20261101_classify_programs.sql）
VISIBLE_PROGRAM_CONDITION = "(dp.excluded_reason IS NULL OR dp.excluded_reason = 'needs_review')"

app = FastAPI(title="学位検索API", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent.parent
WEBPAGE_DIR = BASE_DIR / "webpage"

if WEBPAGE_DIR.exists():
    app.mount("/webpage", StaticFiles(directory=str(WEBPAGE_DIR)), name="webpage")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root_page():
    if not WEBPAGE_DIR.exists():
        raise HTTPException(status_code=404, detail="webpage directory not found")
    return FileResponse(str(WEBPAGE_DIR / "index.html"))


@app.get("/detail")
def detail_page():
    if not WEBPAGE_DIR.exists():
        raise HTTPException(status_code=404, detail="webpage directory not found")
    return FileResponse(str(WEBPAGE_DIR / "detail.html"))


def get_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            set_search_path(cur, get_public_schema())
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")

def close_connection(conn):
    try:
        conn.close()
    except Exception as e:
        print(f"Failed to close connection: {str(e)}")

@app.get("/search")
def search_programs(
    keyword: Optional[str] = None,
    country: Optional[str] = None,
    degree: Optional[str] = None,
    category: Optional[str] = None,
    is_online: Optional[bool] = None,
    tuition_type: Optional[str] = None,
    fixed_only: bool = Query(False),
    non_fixed_only: bool = Query(False),
    price_min: Optional[int] = Query(None, ge=0),
    price_max: Optional[int] = Query(None, ge=0),
    currency: Optional[str] = None,
    with_total: bool = Query(False),
    sort_by: str = Query("last_seen", pattern="^(last_seen|amount|normalized_monthly_amount)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    if fixed_only and non_fixed_only:
        raise HTTPException(
            status_code=400,
            detail="fixed_only and non_fixed_only cannot both be true"
        )

    if price_min is not None and price_max is not None:
        if price_min > price_max:
            raise HTTPException(
                status_code=400,
                detail="price_min must be <= price_max"
            )

    query = f"""
        SELECT
            dp.id,
            dp.program_name,
            u.country,
            u.name AS university_name,
            tp.degree_level,
            dp.course_type,
            tp.amount,
            tp.currency,
            tp.tuition_type,
            tp.normalized_monthly_amount,
            tp.normalization_note,
            dp.is_online,
            dp.last_seen,
            dp.source_url
        FROM {DEGREE_PROGRAMS_TABLE} dp
        JOIN {UNIVERSITIES_TABLE} u ON dp.university_id = u.id
        LEFT JOIN {PROGRAM_TUITION_MAP_TABLE} ptm ON ptm.degree_program_id = dp.id
        LEFT JOIN {TUITION_PATTERNS_TABLE} tp ON ptm.tuition_pattern_id = tp.id
        WHERE {VISIBLE_PROGRAM_CONDITION}
    """

    params = []
    if keyword:
        query += """
            AND (
                dp.program_name ILIKE %s
                OR COALESCE(tp.degree_level, '') ILIKE %s
                OR dp.course_type ILIKE %s
            )
        """
        word = f"%{keyword}%"
        params.extend([word, word, word])
    if country:
        query += " AND u.country = %s"
        params.append(country)
    if degree:
        query += " AND tp.degree_level = %s"
        params.append(degree)
    if category:
        query += " AND dp.course_type = %s"
        params.append(category)
    if is_online is not None:
        query += " AND dp.is_online = %s"
        params.append(is_online)
    if tuition_type:
        query += " AND tp.tuition_type = %s"
        params.append(tuition_type)
    if fixed_only:
        query += " AND tp.tuition_type IN (%s, %s, %s)"
        params.extend(["fixed_year", "fixed_month", "fixed_semester"])
    if non_fixed_only:
        query += " AND tp.tuition_type NOT IN (%s, %s, %s)"
        params.extend(["fixed_year", "fixed_month", "fixed_semester"])
    if currency:
        query += " AND tp.currency = %s"
        params.append(currency)
    if price_min is not None:
        query += " AND tp.amount >= %s"
        params.append(price_min)
    if price_max is not None:
        query += " AND tp.amount <= %s"
        params.append(price_max)

    sort_columns = {
        "last_seen": "dp.last_seen",
        "amount": "tp.amount",
        "normalized_monthly_amount": "tp.normalized_monthly_amount",
    }

    count_params = tuple(params)
    count_query = f"SELECT COUNT(DISTINCT id) FROM ({query}) AS filtered"

    order_col = sort_columns.get(sort_by, "dp.last_seen")
    order_dir = "ASC" if sort_order == "asc" else "DESC"
    query += f" ORDER BY {order_col} {order_dir} NULLS LAST LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            total_count = None
            if with_total:
                cur.execute(count_query, count_params)
                total_count = cur.fetchone()[0]

            cur.execute(query, tuple(params))
            results = cur.fetchall()

            items = [
                {
                    "id": row[0],
                    "program_name": row[1],
                    "country": row[2],
                    "university_name": row[3],
                    "degree_level": row[4],
                    "course_type": row[5],
                    "amount": row[6],
                    "currency": row[7],
                    "tuition_type": row[8],
                    "normalized_monthly_amount": float(row[9]) if row[9] is not None else None,
                    "normalization_note": row[10],
                    "is_online": row[11],
                    "last_seen": row[12].isoformat() if row[12] else None,
                    "source_url": row[13]
                }
                for row in results
            ]

            if with_total:
                return {
                    "items": items,
                    "total": total_count,
                }

            return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)


@app.get("/program/{program_id}")
def get_program(program_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    dp.id,
                    dp.program_name,
                    dp.course_type,
                    dp.is_online,
                    dp.source_url,
                    dp.last_seen,
                    u.name AS university_name,
                    u.country
                FROM {DEGREE_PROGRAMS_TABLE} dp
                JOIN {UNIVERSITIES_TABLE} u ON dp.university_id = u.id
                WHERE dp.id = %s AND {VISIBLE_PROGRAM_CONDITION}
            """, (program_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Program not found")
            program = {
                "id": row[0],
                "program_name": row[1],
                "course_type": row[2],
                "is_online": row[3],
                "source_url": row[4],
                "last_seen": row[5].isoformat() if row[5] else None,
                "university_name": row[6],
                "country": row[7],
            }

            cur.execute(f"""
                SELECT
                    tp.degree_level,
                    tp.amount,
                    tp.currency,
                    tp.tuition_type,
                    tp.normalized_monthly_amount,
                    tp.normalization_note
                FROM {TUITION_PATTERNS_TABLE} tp
                JOIN {PROGRAM_TUITION_MAP_TABLE} ptm ON ptm.tuition_pattern_id = tp.id
                WHERE ptm.degree_program_id = %s
                ORDER BY tp.tuition_type, tp.amount NULLS LAST
            """, (program_id,))
            program["tuition_records"] = [
                {
                    "degree_level": r[0],
                    "amount": r[1],
                    "currency": r[2],
                    "tuition_type": r[3],
                    "normalized_monthly_amount": float(r[4]) if r[4] is not None else None,
                    "normalization_note": r[5],
                }
                for r in cur.fetchall()
            ]
            return program
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)


@app.get("/countries")
def get_countries():
    """国一覧（フロントエンドのドロップダウン用）"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT u.country
                FROM {UNIVERSITIES_TABLE} u
                JOIN {DEGREE_PROGRAMS_TABLE} dp ON dp.university_id = u.id
                WHERE u.country IS NOT NULL AND {VISIBLE_PROGRAM_CONDITION}
                ORDER BY u.country
                """
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)


@app.get("/degree-levels")
def get_degree_levels():
    """学位レベル一覧（フロントエンドのドロップダウン用）"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT degree_level FROM {TUITION_PATTERNS_TABLE} WHERE degree_level IS NOT NULL ORDER BY degree_level"
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)


@app.get("/currencies")
def get_currencies():
    """通貨一覧（フロントエンドのドロップダウン用）"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT currency FROM {TUITION_PATTERNS_TABLE} WHERE currency IS NOT NULL ORDER BY currency"
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)


@app.get("/tuition-types")
def get_tuition_types():
    """料金タイプ一覧（フロントエンドのドロップダウン用）"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT tuition_type FROM {TUITION_PATTERNS_TABLE} WHERE tuition_type IS NOT NULL ORDER BY tuition_type"
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SQL execution failed: {str(e)}")
    finally:
        close_connection(conn)
