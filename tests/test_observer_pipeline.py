#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_observer_pipeline.py
observer パイプラインが永続ログへ結果を渡せることを確認する。
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.SimpleNamespace(Error=Exception)
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

from dataclass.dataclass import ContentType, PageAnalysis, SeedTransformInput
from observer.observe_supervisor import InMemoryObserveQueue, ObserveRunResult, ObserveStackItem
from observer.observe_log import ObserveLogStore
from db.schema_config import get_table_ref

SEED_OBSERVE_RESULTS_TABLE = get_table_ref("SEED_OBSERVE_RESULTS_TABLE")
from observer.observer import run_observer_pipeline


class _DummyLogStore:
    def __init__(self) -> None:
        self.created = []
        self.inserted = []
        self.finished = []
        self.closed = False

    def init_db(self) -> None:
        return None

    def create_run(self, record):
        self.created.append(record)
        return 101

    def insert_result(self, run_id, **kwargs):
        self.inserted.append((run_id, kwargs))

    def finish_run(self, run_id, record):
        self.finished.append((run_id, record))

    def close(self) -> None:
        self.closed = True


class _DummyCursor:
    def __init__(self) -> None:
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return (1,)

    def close(self) -> None:
        return None


class _DummyConn:
    def __init__(self) -> None:
        self.cursor_obj = _DummyCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class TestObserverPipeline(unittest.TestCase):
    def test_observe_log_uses_result_source_domain(self):
        page = PageAnalysis(content_type=ContentType.HTML)
        item = ObserveStackItem(
            source_url="https://example.edu/list",
            page_analysis=page,
            university_names=[],
            request_logs=[],
            observe_run_id=7,
        )
        result = SeedTransformInput(
            source_url="https://example.edu/list",
            source_domain="example.edu",
            university_names=[],
            hits=[],
            root_seed_urls=[],
            detailed_seed_urls=[],
            course_list_found=False,
            recommended_depth=3,
            duplicate_root_urls=[],
            errors=[],
            api_type="brave",
            first_search_count=0,
            internal_link_extracted_count=0,
            fallback_executed=False,
            api_usage_count=0,
            run_id=7,
            source_stage="seed_searcher",
        )
        dummy_conn = _DummyConn()
        store = ObserveLogStore(pg_dsn="postgresql://example", schema_path="ControlPlane/seed_observe_log_schema.sql")

        with patch.object(store, "_connect", return_value=dummy_conn):
            store.insert_result(
                1,
                external_run_id=7,
                source_stage="seed_searcher",
                item=item,
                result=result,
                request_log_count=0,
            )

        executed_sql, params = dummy_conn.cursor_obj.executed[0]
        self.assertIn(f"INSERT INTO {SEED_OBSERVE_RESULTS_TABLE}", executed_sql)
        self.assertEqual(params[4], "example.edu")

    def test_pipeline_writes_observe_logs(self):
        page = PageAnalysis(content_type=ContentType.HTML)
        page.extracted_universitynamelist = ["Test University"]
        item = ObserveStackItem(
            source_url="https://example.edu/list",
            page_analysis=page,
            university_names=["Test University"],
            request_logs=[{"url": "https://example.edu/list"}],
            observe_run_id=7,
        )
        queue = InMemoryObserveQueue()
        queue.push(item)

        transformed = SeedTransformInput(
            source_url="https://example.edu/list",
            source_domain="example.edu",
            university_names=["Test University"],
            hits=[],
            root_seed_urls=["https://example.edu"],
            detailed_seed_urls=["https://example.edu/programmes"],
            course_list_found=True,
            recommended_depth=1,
            duplicate_root_urls=[],
            errors=[],
            api_type="brave",
            first_search_count=3,
            internal_link_extracted_count=1,
            fallback_executed=False,
            api_usage_count=2,
            run_id=7,
            source_stage="seed_searcher",
        )
        log_store = _DummyLogStore()

        with (
            patch("observer.observer.ObserveLogStore.from_env", return_value=log_store),
            patch("observer.observer.run_supervisor", return_value=([ObserveRunResult(url=item.source_url, added_log_count=1, error_count=0, stacked_for_searcher=True)], queue)),
            patch("observer.observer.handle_observe_item", return_value=transformed),
            patch(
                "observer.observer.promote_high_quality_targets_from_stage",
                return_value=SimpleNamespace(
                    scanned_rows=1,
                    accepted_rows=1,
                    promoted_targets=1,
                    stage_source="PARENT_DB_OWNER_CONNECTION",
                    target_source="PARENT_DB_OWNER_CONNECTION",
                ),
            ),
        ):
            summary = run_observer_pipeline(source_urls=[item.source_url], observe_run_id=7)

        self.assertEqual(summary.observed_urls, 1)
        self.assertEqual(summary.added_targets, 1)
        self.assertEqual(summary.promoted_targets, 1)
        self.assertEqual(len(log_store.created), 1)
        self.assertEqual(len(log_store.inserted), 1)
        self.assertEqual(len(log_store.finished), 1)
        self.assertTrue(log_store.closed)
        self.assertEqual(log_store.created[0].external_run_id, 7)
        self.assertEqual(log_store.inserted[0][0], 101)
        self.assertEqual(log_store.inserted[0][1]["request_log_count"], 1)
        self.assertEqual(log_store.finished[0][0], 101)
        self.assertEqual(log_store.finished[0][1].added_targets_count, 1)

    def test_pipeline_continues_when_insert_result_fails(self):
        page = PageAnalysis(content_type=ContentType.HTML)
        page.extracted_universitynamelist = ["Test University"]
        item = ObserveStackItem(
            source_url="https://example.edu/list",
            page_analysis=page,
            university_names=["Test University"],
            request_logs=[{"url": "https://example.edu/list"}],
            observe_run_id=7,
        )
        queue = InMemoryObserveQueue()
        queue.push(item)

        transformed = SeedTransformInput(
            source_url="https://example.edu/list",
            source_domain="example.edu",
            university_names=["Test University"],
            hits=[],
            root_seed_urls=["https://example.edu"],
            detailed_seed_urls=["https://example.edu/programmes"],
            course_list_found=True,
            recommended_depth=1,
            duplicate_root_urls=[],
            errors=[],
            api_type="brave",
            first_search_count=3,
            internal_link_extracted_count=1,
            fallback_executed=False,
            api_usage_count=2,
            run_id=7,
            source_stage="seed_searcher",
        )
        log_store = _DummyLogStore()

        def _raise_insert(*args, **kwargs):
            raise RuntimeError("insert failed")

        log_store.insert_result = _raise_insert

        with (
            patch("observer.observer.ObserveLogStore.from_env", return_value=log_store),
            patch("observer.observer.run_supervisor", return_value=([ObserveRunResult(url=item.source_url, added_log_count=1, error_count=0, stacked_for_searcher=True)], queue)),
            patch("observer.observer.handle_observe_item", return_value=transformed),
            patch(
                "observer.observer.promote_high_quality_targets_from_stage",
                return_value=SimpleNamespace(
                    scanned_rows=1,
                    accepted_rows=1,
                    promoted_targets=1,
                    stage_source="PARENT_DB_OWNER_CONNECTION",
                    target_source="PARENT_DB_OWNER_CONNECTION",
                ),
            ),
        ):
            summary = run_observer_pipeline(source_urls=[item.source_url], observe_run_id=7)

        self.assertEqual(summary.added_targets, 1)
        self.assertEqual(summary.promoted_targets, 1)
        self.assertEqual(len(log_store.finished), 1)
        self.assertTrue(log_store.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
