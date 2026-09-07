import os
import unittest
from unittest.mock import patch

os.environ.setdefault("CRAWL_RESOURCE_SAMPLES_TABLE", "etl.crawl_resource_samples")

from ControlPlane.resource_recorder import _ResourceSampleWriter


class FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def executemany(self, query, rows):
        self.executed.append((query, list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConnection:
    def __init__(self):
        self.closed = 0
        self.committed = False
        self.rolled_back = False
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cursor = FakeCursor()
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = 1


class TestResourceSampleWriter(unittest.TestCase):
    def setUp(self):
        self.fake_connection = FakeConnection()
        patcher = patch("ControlPlane.resource_recorder.psycopg2")
        fake_psycopg2 = patcher.start()
        fake_psycopg2.connect.return_value = self.fake_connection
        self.addCleanup(patcher.stop)

    def _make_writer(self, batch_size=3):
        writer = _ResourceSampleWriter(
            pg_dsn="postgresql://unused", table_ref='"etl"."crawl_resource_samples"', batch_size=batch_size
        )
        return writer, self.fake_connection

    def test_add_does_not_flush_before_batch_size(self):
        writer, fake_connection = self._make_writer(batch_size=3)

        writer.add((123, "run1", "hostA", 1.0, 10.0, 100.0, 5.0, 40.0))
        writer.add((123, "run1", "hostA", 2.0, 12.0, 101.0, 6.0, 41.0))

        self.assertEqual(len(writer._buffer), 2)
        self.assertFalse(fake_connection.committed)

    def test_add_flushes_once_batch_size_reached(self):
        writer, fake_connection = self._make_writer(batch_size=2)

        writer.add((123, "run1", "hostA", 1.0, 10.0, 100.0, 5.0, 40.0))
        writer.add((123, "run1", "hostA", 2.0, 12.0, 101.0, 6.0, 41.0))

        self.assertEqual(writer._buffer, [])
        self.assertTrue(fake_connection.committed)

    def test_close_flushes_remaining_buffer(self):
        writer, fake_connection = self._make_writer(batch_size=10)

        writer.add((123, "run1", "hostA", 1.0, 10.0, 100.0, 5.0, 40.0))
        writer.close()

        self.assertEqual(writer._buffer, [])
        self.assertTrue(fake_connection.committed)
        self.assertEqual(fake_connection.closed, 1)


if __name__ == "__main__":
    unittest.main()
