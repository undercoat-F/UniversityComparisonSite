from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    import psycopg2
except Exception:  # pragma: no cover
    psycopg2 = None

from dataclass.dataclass import SeedTransformInput
from db.schema_config import get_observer_schema, get_public_schema, get_table_ref, set_search_path
from observer.seed_transformer import to_adder_targets_batch

SEED_URLS_TABLE = get_table_ref("SEED_URLS_TABLE")
SEED_OBSERVE_RESULTS_TABLE = get_table_ref("SEED_OBSERVE_RESULTS_TABLE")

try:
    from ControlPlane import init_seed_db as init_seed_db
except Exception:  # pragma: no cover
    init_seed_db = None


@dataclass
class SeedPromotionSummary:
    scanned_rows: int
    accepted_rows: int
    promoted_targets: int
    stage_source: str
    target_source: str


BLOCKED_SEED_DOMAINS = {
    "wikipedia.org",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "zoominfo.com",
    "tiktok.com",
    "kotobank.jp",
}


def _pick_stage_dsn() -> tuple[str, str]:
    for env_name in ("PARENT_DB_OWNER_CONNECTION",):
        value = os.getenv(env_name, "").strip()
        if value:
            return value, env_name

    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if all((os.getenv(name, "").strip() for name in required)):
        host = os.getenv("DB_HOST", "").strip()
        dbname = os.getenv("DB_NAME", "").strip()
        user = os.getenv("DB_USER", "").strip()
        password = os.getenv("DB_PASSWORD", "").strip()
        port = os.getenv("DB_PORT", "5432").strip() or "5432"
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"
        return dsn, "DB_*"

    raise RuntimeError(
        "No stage DSN found. Set PARENT_DB_OWNER_CONNECTION or DB_* vars."
    )


def _pick_target_params_or_dsn() -> tuple[str | None, dict[str, object] | None, str]:
    for env_name in ("PARENT_DB_OWNER_CONNECTION",):
        value = os.getenv(env_name, "").strip()
        if value:
            return value, None, env_name

    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if all((os.getenv(name, "").strip() for name in required)):
        host = os.getenv("DB_HOST", "").strip()
        dbname = os.getenv("DB_NAME", "").strip()
        user = os.getenv("DB_USER", "").strip()
        password = os.getenv("DB_PASSWORD", "").strip()
        port = os.getenv("DB_PORT", "5432").strip() or "5432"
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"
        return dsn, None, "DB_*"

    raise RuntimeError(
        "No target DSN found. Set PARENT_DB_OWNER_CONNECTION or DB_* vars."
    )


def _json_to_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v]
        except json.JSONDecodeError:
            pass
    return []


def _root_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.netloc:
        return ""
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}"


def _is_blocked_seed_domain(domain: str) -> bool:
    d = (domain or "").strip().lower()
    if not d:
        return False
    for blocked in BLOCKED_SEED_DOMAINS:
        if d == blocked or d.endswith(f".{blocked}"):
            return True
    return False


def _collect_roots_from_row(source_url: str, root_seed_urls: object, detailed_seed_urls: object) -> list[str]:
    roots: list[str] = []
    for url in _json_to_list(root_seed_urls) + _json_to_list(detailed_seed_urls):
        root = _root_url(url)
        if root and root not in roots:
            roots.append(root)

    source_root = _root_url(source_url)
    if source_root and source_root not in roots:
        roots.append(source_root)

    return roots


def _safe_depth(recommended_depth: int, *, max_depth: int) -> int:
    if recommended_depth < 1:
        return 1
    if recommended_depth > max_depth:
        return max_depth
    return recommended_depth


def _stage_rows_to_targets(
    rows: list[tuple[object, ...]],
    *,
    min_hit_count: int,
    max_error_count: int,
    max_recommended_depth: int,
) -> tuple[list[tuple[str, int]], int, int]:
    targets: list[tuple[str, int]] = []
    seen_roots: set[str] = set()
    scanned = 0
    accepted = 0

    for row in rows:
        (
            source_url,
            root_seed_urls,
            detailed_seed_urls,
            course_list_found,
            recommended_depth,
            hit_count,
            error_count,
        ) = row
        scanned += 1

        if not bool(course_list_found):
            continue
        if int(hit_count) < int(min_hit_count):
            continue
        if int(error_count) > int(max_error_count):
            continue
        if int(recommended_depth) > int(max_recommended_depth):
            continue

        depth = _safe_depth(int(recommended_depth), max_depth=int(max_recommended_depth))
        row_added = False
        for root in _collect_roots_from_row(str(source_url), root_seed_urls, detailed_seed_urls):
            domain = urlparse(root).netloc.lower()
            if _is_blocked_seed_domain(domain):
                continue
            if root in seen_roots:
                continue
            seen_roots.add(root)
            targets.append((root, depth))
            row_added = True

        if row_added:
            accepted += 1

    return targets, scanned, accepted


def _upsert_targets_with_connection(conn, targets: list[tuple[str, int]]) -> int:
    if not targets:
        return 0

    db_module = init_seed_db
    if db_module is None:
        from ControlPlane import init_seed_db as db_module

    with conn.cursor() as cursor:
        set_search_path(cursor, get_observer_schema(), get_public_schema())
        for root_url, depth in targets:
            domain = urlparse(root_url).netloc
            country = db_module.infer_country(root_url)
            cursor.execute(
                f"""
                INSERT INTO {SEED_URLS_TABLE} (country, domain, root_url, depth)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(domain, root_url) DO UPDATE SET
                    country=EXCLUDED.country,
                    depth=EXCLUDED.depth,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (country, domain, root_url, depth),
            )
    conn.commit()
    return len(targets)


def promote_high_quality_targets_from_stage(
    *,
    observe_log_run_id: int | None = None,
    external_run_id: int | None = None,
    min_hit_count: int = 8,
    max_error_count: int = 3,
    max_recommended_depth: int = 2,
) -> SeedPromotionSummary:
    """Promote high-quality observer results from stage DB to target seed_urls DB.

    Stage DB: observer result store (typically PARENT_DB_OWNER_CONNECTION)
    Target DB: ETL seed_urls store (typically PARENT_DB_OWNER_CONNECTION or DB_*)
    """
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required for DB promotion. Install psycopg2-binary.")

    stage_dsn, stage_source = _pick_stage_dsn()
    target_dsn, target_params, target_source = _pick_target_params_or_dsn()

    with psycopg2.connect(stage_dsn) as stage_conn:
        with stage_conn.cursor() as stage_cur:
            set_search_path(stage_cur, get_observer_schema(), get_public_schema())
            stage_cur.execute(
                f"""
                SELECT
                    source_url,
                    root_seed_urls,
                    detailed_seed_urls,
                    course_list_found,
                    recommended_depth,
                    hit_count,
                    error_count
                FROM {SEED_OBSERVE_RESULTS_TABLE}
                WHERE (%s::bigint IS NULL OR run_id = %s)
                  AND (%s::bigint IS NULL OR external_run_id = %s)
                ORDER BY id DESC
                """,
                (observe_log_run_id, observe_log_run_id, external_run_id, external_run_id),
            )
            stage_rows = stage_cur.fetchall()
    targets, scanned, accepted = _stage_rows_to_targets(
        stage_rows,
        min_hit_count=min_hit_count,
        max_error_count=max_error_count,
        max_recommended_depth=max_recommended_depth,
    )

    # Mirror write: stage DB and target DB are handled independently when they differ.
    if stage_dsn == target_dsn:
        with psycopg2.connect(stage_dsn) as shared_conn:
            promoted = _upsert_targets_with_connection(shared_conn, targets)
    else:
        with psycopg2.connect(stage_dsn) as observer_seed_conn:
            _upsert_targets_with_connection(observer_seed_conn, targets)

        if target_dsn:
            with psycopg2.connect(target_dsn) as target_conn:
                promoted = _upsert_targets_with_connection(target_conn, targets)
        else:
            with psycopg2.connect(**target_params) as target_conn:
                promoted = _upsert_targets_with_connection(target_conn, targets)

    return SeedPromotionSummary(
        scanned_rows=scanned,
        accepted_rows=accepted,
        promoted_targets=promoted,
        stage_source=stage_source,
        target_source=target_source,
    )


def add_seed_targets(items: list[SeedTransformInput], *, ensure_schema: bool = False) -> int:
    """Transform items to (root_url, depth) targets and upsert into ETL seed_urls DB.

    Returns:
        Number of targets passed to upsert.
    """
    targets = to_adder_targets_batch(items)
    if not targets:
        return 0

    db_module = init_seed_db
    if db_module is None:
        from ControlPlane import init_seed_db as db_module

    if ensure_schema:
        db_module.init_db()

    try:
        db_module.upsert_targets(targets)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        missing_seed_table = (
            "relation \"seed_urls\" does not exist" in msg
            or "UndefinedTable" in type(exc).__name__
        )
        if not ensure_schema and missing_seed_table:
            raise RuntimeError(
                "seed_urls table is missing. Run with --init-schema before adding targets."
            ) from exc
        else:
            raise
    return len(targets)

