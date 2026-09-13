#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_seed_adder.py
seed_adder のユニットテスト
- transformer 出力を ETL 側 upsert_targets へ渡す
"""

import os
import sys
import types
import unittest
import importlib
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import psycopg2 as _psycopg2  # noqa: F401
except Exception:
    if "psycopg2" not in sys.modules:
        sys.modules["psycopg2"] = types.SimpleNamespace(Error=Exception)

try:
    import dotenv as _dotenv  # noqa: F401
except Exception:
    if "dotenv" not in sys.modules:
        sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

from dataclass.dataclass import SeedTransformInput
from db.schema_config import get_observer_schema, get_table_ref
from ControlPlane import init_seed_db
from observer.seed_adder import _stage_rows_to_targets, add_seed_targets

SEED_URLS_TABLE = get_table_ref("SEED_URLS_TABLE")
SEED_OBSERVE_RESULTS_TABLE = get_table_ref("SEED_OBSERVE_RESULTS_TABLE")


class TestSeedAdder(unittest.TestCase):
    def test_stage_rows_to_targets_accepts_high_quality_rows(self):
        rows = [
            (
                "https://example.edu/list",
                '["https://example.edu"]',
                '["https://example.edu/programmes"]',
                True,
                1,
                12,
                0,
            )
        ]

        targets, scanned, accepted = _stage_rows_to_targets(
            rows,
            min_hit_count=8,
            max_error_count=1,
            max_recommended_depth=2,
        )

        self.assertEqual(scanned, 1)
        self.assertEqual(accepted, 1)
        self.assertIn(("https://example.edu", 1), targets)

    def test_stage_rows_to_targets_rejects_low_quality_rows(self):
        rows = [
            (
                "https://weak.example.edu/list",
                '["https://weak.example.edu"]',
                '[]',
                False,
                3,
                1,
                3,
            )
        ]

        targets, scanned, accepted = _stage_rows_to_targets(
            rows,
            min_hit_count=8,
            max_error_count=1,
            max_recommended_depth=2,
        )

        self.assertEqual(scanned, 1)
        self.assertEqual(accepted, 0)
        self.assertEqual(targets, [])

    def test_stage_rows_to_targets_rejects_blocked_noise_domains(self):
        rows = [
            (
                "https://www.zoominfo.com/noise-list",
                '["https://www.zoominfo.com"]',
                '["https://www.facebook.com/noise"]',
                True,
                1,
                20,
                0,
            )
        ]

        targets, scanned, accepted = _stage_rows_to_targets(
            rows,
            min_hit_count=8,
            max_error_count=1,
            max_recommended_depth=2,
        )

        self.assertEqual(scanned, 1)
        self.assertEqual(accepted, 0)
        self.assertEqual(targets, [])

    def test_stage_rows_to_targets_keeps_allowed_domains_only(self):
        rows = [
            (
                "https://source.example.edu/list",
                '["https://www.zoominfo.com", "https://www.example.edu"]',
                '["https://www.example.edu/programmes"]',
                True,
                1,
                20,
                0,
            )
        ]

        targets, scanned, accepted = _stage_rows_to_targets(
            rows,
            min_hit_count=8,
            max_error_count=1,
            max_recommended_depth=2,
        )

        self.assertEqual(scanned, 1)
        self.assertEqual(accepted, 1)
        self.assertIn(("https://www.example.edu", 1), targets)
        self.assertFalse(any("zoominfo.com" in root for root, _ in targets))

    @unittest.skipUnless(
        os.getenv("RUN_DB_TARGET_CHECK", "0").strip().lower() in {"1", "true", "yes", "on"},
        "Set RUN_DB_TARGET_CHECK=1 to run DB target diagnostic test.",
    )
    def test_db_connection_target_info(self):
        """接続先DBの識別情報を表示し、seed_urls の可視性を確認する診断テスト。"""
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        env_file = os.getenv("DB_TARGET_ENV_FILE", os.path.join(workspace_root, ".env"))
        if os.path.exists(env_file):
            # 診断時は明示指定した .env を優先して読み込む
            init_seed_db.load_dotenv(dotenv_path=env_file, override=True, encoding="utf-8-sig")
            print("\n[DB_TARGET_CHECK] env_file=", env_file)
        else:
            print("\n[DB_TARGET_CHECK] env_file_not_found=", env_file)

        # 通常テストでは psycopg2 をスタブ化しているため、診断時は実モジュールへ差し替える。
        try:
            current = sys.modules.get("psycopg2")
            if current is not None and not hasattr(current, "connect"):
                del sys.modules["psycopg2"]
            real_psycopg2 = importlib.import_module("psycopg2")
            init_seed_db.psycopg2 = real_psycopg2
        except Exception:
            pass

        psycopg2_mod = getattr(init_seed_db, "psycopg2", None)
        if not hasattr(psycopg2_mod, "connect"):
            self.fail("psycopg2.connect is unavailable. Install psycopg2-binary in this environment.")

        parent_owner = os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
        dsn_candidates = [
            ("PARENT_DB_OWNER_CONNECTION", parent_owner, SEED_OBSERVE_RESULTS_TABLE),
            ("PARENT_DB_OWNER_CONNECTION", parent_owner, SEED_URLS_TABLE),
        ]

        missing = [name for name, value, _ in dsn_candidates if not value]
        if missing:
            self.fail(f"Missing required DSN(s): {', '.join(missing)}")

        checked_targets = []
        invisible_targets = []
        for dsn_name, dsn_value, expected_table in dsn_candidates:
            parsed = urlparse(dsn_value)
            print(f"\n[DB_TARGET_CHECK] ==== TARGET: {dsn_name} ====")
            print("[DB_TARGET_CHECK] connection_source=", dsn_name)
            print("[DB_TARGET_CHECK] dsn_host=", parsed.hostname)
            print("[DB_TARGET_CHECK] dsn_dbname=", (parsed.path or "").lstrip("/"))
            print("[DB_TARGET_CHECK] dsn_user=", parsed.username)
            print("[DB_TARGET_CHECK] dsn_port=", parsed.port)

            conn = psycopg2_mod.connect(dsn_value)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                          current_database(),
                          current_user,
                          current_schema(),
                          current_setting('search_path'),
                          inet_server_addr()::text,
                          inet_server_port(),
                          version(),
                                                    to_regclass(%s)::text
                        """,
                    (expected_table,),)
                    (
                        current_db,
                        current_user,
                        current_schema,
                        search_path,
                        server_addr,
                        server_port,
                        server_version,
                        seed_urls_regclass,
                    ) = cur.fetchone()

            print("[DB_TARGET_CHECK] dbname=", current_db)
            print("[DB_TARGET_CHECK] user=", current_user)
            print("[DB_TARGET_CHECK] current_schema=", current_schema)
            print("[DB_TARGET_CHECK] search_path=", search_path)
            print("[DB_TARGET_CHECK] server_addr=", server_addr)
            print("[DB_TARGET_CHECK] server_port=", server_port)
            print("[DB_TARGET_CHECK] version=", server_version)
            print(f"[DB_TARGET_CHECK] {expected_table}=", seed_urls_regclass)

            # Neon は同一プロジェクト内でも branch ごとに endpoint(host)が異なる。
            # どの endpoint に接続したかを出しておくと branch 取り違えの切り分けが容易。
            host = parsed.hostname or ""
            print("[DB_TARGET_CHECK] neon_endpoint_host=", host)
            if host.startswith("ep-"):
                print("[DB_TARGET_CHECK] neon_endpoint_hint=", host.split(".")[0])

            self.assertTrue(bool(current_db), f"{dsn_name}: current_database() is empty")
            if seed_urls_regclass:
                print("[DB_TARGET_CHECK] RESULT=PASS")
            else:
                print("[DB_TARGET_CHECK] RESULT=WARN table_not_visible")
                invisible_targets.append((dsn_name, expected_table))
            checked_targets.append(dsn_name)

        if invisible_targets:
            for dsn_name, table_name in invisible_targets:
                print(f"[DB_TARGET_CHECK] WARN: {dsn_name} cannot see {table_name}")
            print("\n[DB_TARGET_CHECK] OVERALL=WARN")
        else:
            print("\n[DB_TARGET_CHECK] OVERALL=PASS")
        print("[DB_TARGET_CHECK] checked_targets=", ", ".join(checked_targets))

    def test_add_seed_targets_calls_upsert_targets(self):
        with (
            patch("observer.seed_adder.to_adder_targets_batch", return_value=[("https://www.example.edu", 1)]) as m_transform,
            patch("observer.seed_adder.init_seed_db.upsert_targets") as m_upsert,
        ):
            count = add_seed_targets([SeedTransformInput(
                source_url="https://www.example.edu",
                source_domain="www.example.edu",
                university_names=[],
                hits=[],
                root_seed_urls=[],
                detailed_seed_urls=[],
                course_list_found=False,
                recommended_depth=3,
                duplicate_root_urls=[],
            )])

        self.assertEqual(count, 1)
        m_transform.assert_called_once()
        m_upsert.assert_called_once_with([("https://www.example.edu", 1)])

    def test_add_seed_targets_skips_upsert_when_empty(self):
        with (
            patch("observer.seed_adder.to_adder_targets_batch", return_value=[]) as m_transform,
            patch("observer.seed_adder.init_seed_db.upsert_targets") as m_upsert,
        ):
            count = add_seed_targets([])

        self.assertEqual(count, 0)
        m_transform.assert_called_once_with([])
        m_upsert.assert_not_called()

    def test_add_seed_targets_can_init_schema(self):
        with (
            patch("observer.seed_adder.to_adder_targets_batch", return_value=[("https://www.example.edu", 2)]),
            patch("observer.seed_adder.init_seed_db.init_db") as m_init,
            patch("observer.seed_adder.init_seed_db.upsert_targets") as m_upsert,
        ):
            count = add_seed_targets([], ensure_schema=True)

        self.assertEqual(count, 1)
        m_init.assert_called_once()
        m_upsert.assert_called_once_with([("https://www.example.edu", 2)])

    def test_add_seed_targets_raises_on_missing_seed_table(self):
        missing_table_error = RuntimeError('relation "seed_urls" does not exist')
        with (
            patch("observer.seed_adder.to_adder_targets_batch", return_value=[("https://www.example.edu", 2)]),
            patch("observer.seed_adder.init_seed_db.init_db") as m_init,
            patch("observer.seed_adder.init_seed_db.upsert_targets", side_effect=missing_table_error),
        ):
            with self.assertRaises(RuntimeError) as cm:
                add_seed_targets([], ensure_schema=False)

        self.assertIn("seed_urls table is missing", str(cm.exception))
        m_init.assert_not_called()

    def test_add_seed_targets_raises_on_non_schema_error(self):
        with (
            patch("observer.seed_adder.to_adder_targets_batch", return_value=[("https://www.example.edu", 2)]),
            patch("observer.seed_adder.init_seed_db.init_db") as m_init,
            patch("observer.seed_adder.init_seed_db.upsert_targets", side_effect=RuntimeError("permission denied")),
        ):
            with self.assertRaises(RuntimeError):
                add_seed_targets([], ensure_schema=False)

        m_init.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

