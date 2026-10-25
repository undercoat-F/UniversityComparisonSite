import unittest
from unittest.mock import patch

from db import db_saver


class FakeCursor:
    def __init__(self):
        self.returned_rows = []

    def fetchall(self):
        return self.returned_rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass

    def rollback(self):
        pass


class TestProgramUpsert(unittest.TestCase):
    def test_deduplicates_a_chunk_and_preserves_all_id_mappings(self):
        conn = FakeConnection()
        rows = [
            {
                "id": "program-1",
                "university_id": "university-1",
                "program_name": "Computer Science",
                "course_type": "general",
                "is_online": "0",
                "source_url": "https://example.edu/computer-science",
                "last_seen": "2026-08-19 00:00:00",
                "quality_flag": "high",
            },
            {
                "id": "program-2",
                "university_id": "university-1",
                "program_name": "Computer Science",
                "course_type": "general",
                "is_online": "0",
                "source_url": "https://example.edu/computer-science",
                "last_seen": "2026-08-19 00:00:00",
                "quality_flag": "high",
            },
        ]

        def fake_execute_values(cursor, sql_stmt, payload):
            self.assertEqual(len(payload), 1)
            cursor.returned_rows = [(payload[0][0], 42)]

        with patch.object(db_saver, "execute_values", side_effect=fake_execute_values):
            inserted, skipped, id_map = db_saver._insert_programs_rows(
                conn,
                rows,
                {"university-1": 7},
            )

        self.assertEqual(inserted, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(id_map, {"program-1": 42, "program-2": 42})

def _pattern_row(csv_id, amount_min, degree_level="Bachelor"):
    return {
        "id": csv_id,
        "degree_level": degree_level,
        "amount": "9000",
        "currency": "GBP",
        "fee_type": "tuition",
        "tuition_type": "fixed_year",
        "amount_min": amount_min,
        "amount_max": "",
        "normalized_monthly_amount": "",
        "normalization_note": "yearly_div_12",
    }


class TestPatternUpsert(unittest.TestCase):
    def test_merges_rows_with_the_same_conflict_key_and_maps_all_ids(self):
        conn = FakeConnection()
        # amount_min だけが異なる2行は一意制約上は同じ行になるため、1回の INSERT に両方含めてはいけない
        rows = [_pattern_row("pattern-1", "8000"), _pattern_row("pattern-2", "8500"), _pattern_row("pattern-3", "", "Master")]

        def fake_execute_values(cursor, sql_stmt, payload):
            self.assertEqual(len(payload), 2)
            self.assertEqual(payload[0][0], "pattern-2")  # 後勝ち
            self.assertEqual(payload[0][6], 8500.0)
            cursor.returned_rows = [(payload[0][0], 10), (payload[1][0], 11)]

        with patch.object(db_saver, "execute_values", side_effect=fake_execute_values):
            inserted, id_map = db_saver._insert_patterns_rows(conn, rows)

        self.assertEqual(inserted, 2)
        self.assertEqual(id_map, {"pattern-1": 10, "pattern-2": 10, "pattern-3": 11})

    def test_does_not_merge_rows_whose_conflict_key_contains_null(self):
        conn = FakeConnection()
        rows = [_pattern_row("pattern-1", "", ""), _pattern_row("pattern-2", "", "")]

        def fake_execute_values(cursor, sql_stmt, payload):
            self.assertEqual(len(payload), 2)
            cursor.returned_rows = [(payload[0][0], 10), (payload[1][0], 11)]

        with patch.object(db_saver, "execute_values", side_effect=fake_execute_values):
            _, id_map = db_saver._insert_patterns_rows(conn, rows)

        self.assertEqual(id_map, {"pattern-1": 10, "pattern-2": 11})

    def test_numeric_columns_are_cast_explicitly(self):
        # まとめた行の値がすべて NULL だと VALUES の列が text と推測されるため、型を明示していること
        conn = FakeConnection()
        captured = {}

        def fake_execute_values(cursor, sql_stmt, payload):
            captured["sql"] = sql_stmt
            cursor.returned_rows = [(payload[0][0], 10)]

        with patch.object(db_saver, "execute_values", side_effect=fake_execute_values):
            db_saver._insert_patterns_rows(conn, [_pattern_row("pattern-1", "")])

        for column in ("amount", "amount_min", "amount_max", "normalized_monthly_amount"):
            self.assertIn(f"src.{column}::numeric", captured["sql"])
