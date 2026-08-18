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