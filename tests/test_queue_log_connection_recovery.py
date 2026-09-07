import os
import unittest

for table_env_name in (
    "CRAWL_RUNS_TABLE",
    "CRAWL_QUEUE_STATE_TABLE",
    "CRAWL_ATTEMPTS_TABLE",
    "CRAWL_EDGES_TABLE",
    "CRAWL_FAILURES_TABLE",
    "CRAWL_TAG_KEYWORD_HITS_TABLE",
    "CRAWL_DOMAIN_TAG_SCORES_TABLE",
    "CRAWL_TAG_CLASS_COUNTS_TABLE",
    "CRAWL_DOMAIN_CLASS_COUNTS_TABLE",
):
    os.environ.setdefault(table_env_name, table_env_name.lower())

from ControlPlane.queue_log import QueueLogStore


class FakeCursor:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = False

    def execute(self, query, params):
        if self.should_fail:
            raise RuntimeError("SSL connection has been closed unexpectedly")

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.closed = 0
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self.should_fail)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = 1


class QueueLogConnectionRecoveryTests(unittest.TestCase):
    def test_finish_run_reconnects_after_dropped_connection(self):
        first_connection = FakeConnection(should_fail=True)
        replacement_connection = FakeConnection()
        store = QueueLogStore(db_path="", schema_path="", pg_dsn="unused")
        connections = iter((first_connection, replacement_connection))

        def connect():
            store._conn = next(connections)
            return store._conn

        store._connect = connect

        store.finish_run(run_id=42, status="completed")

        self.assertTrue(first_connection.rolled_back)
        self.assertEqual(first_connection.closed, 1)
        self.assertTrue(replacement_connection.committed)


if __name__ == "__main__":
    unittest.main()