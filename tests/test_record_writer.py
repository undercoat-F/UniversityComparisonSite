import asyncio
import unittest

from crawler.distributed.record_writer import RecordBatchWriter, sort_row_bundle, to_load_record


class FakeLoader:
    def __init__(self, fail_times: int = 0):
        self.batches = []
        self.fail_times = fail_times
        self.closed = False

    def load(self, records):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("db down")
        self.batches.append([r["url"] for r in records])
        return {"universities": 1}

    def close(self):
        self.closed = True


def _record(i: int) -> dict:
    return {"url": f"https://a.edu/p{i}", "title": "t", "degrees": [], "raw_text": "x" * 1000, "tag_class_counts": [1]}


class TestRecordBatchWriter(unittest.IsolatedAsyncioTestCase):
    async def test_flushes_when_batch_size_is_reached(self):
        loader = FakeLoader()
        writer = RecordBatchWriter(loader, batch_size=3, flush_interval_sec=60, max_pending=10)
        await writer.start()
        for i in range(3):
            await writer.add("a.edu", _record(i))
        for _ in range(50):
            if loader.batches:
                break
            await asyncio.sleep(0.01)
        await writer.close()

        self.assertEqual(loader.batches, [["https://a.edu/p0", "https://a.edu/p1", "https://a.edu/p2"]])
        self.assertEqual(writer.written, 3)
        self.assertTrue(loader.closed)

    async def test_flushes_after_interval(self):
        loader = FakeLoader()
        writer = RecordBatchWriter(loader, batch_size=100, flush_interval_sec=0.05, max_pending=100)
        await writer.start()
        await writer.add("a.edu", _record(0))
        await asyncio.sleep(0.2)

        self.assertEqual(loader.batches, [["https://a.edu/p0"]])
        await writer.close()

    async def test_close_flushes_remaining_records(self):
        loader = FakeLoader()
        writer = RecordBatchWriter(loader, batch_size=100, flush_interval_sec=60, max_pending=100)
        await writer.start()
        await writer.add("a.edu", _record(0))
        await writer.close()

        self.assertEqual(loader.batches, [["https://a.edu/p0"]])
        self.assertEqual(writer.dropped, 0)

    async def test_failed_flush_keeps_records_for_retry(self):
        loader = FakeLoader(fail_times=1)
        writer = RecordBatchWriter(loader, batch_size=100, flush_interval_sec=60, max_pending=100)
        await writer.add("a.edu", _record(0))
        await writer.flush()
        self.assertEqual(writer.pending, 1)
        self.assertEqual(writer.failed_flushes, 1)

        await writer.add("a.edu", _record(1))
        await writer.flush()
        self.assertEqual(loader.batches, [["https://a.edu/p0", "https://a.edu/p1"]])
        self.assertEqual(writer.pending, 0)

    async def test_pending_records_are_bounded_while_db_is_down(self):
        loader = FakeLoader(fail_times=100)
        writer = RecordBatchWriter(loader, batch_size=2, flush_interval_sec=60, max_pending=4)
        for i in range(10):
            await writer.add("a.edu", _record(i))

        self.assertLessEqual(writer.pending, 4)
        self.assertEqual(writer.pending + writer.dropped, 10)
        self.assertEqual(loader.fail_times, 99)  # 障害中は add のたびに再試行しない

    async def test_close_counts_unwritten_records_as_dropped(self):
        loader = FakeLoader(fail_times=100)
        writer = RecordBatchWriter(loader, batch_size=100, flush_interval_sec=60, max_pending=100)
        await writer.add("a.edu", _record(0))
        await writer.close()

        self.assertEqual(writer.dropped, 1)
        self.assertEqual(writer.pending, 0)


class TestRecordHelpers(unittest.TestCase):
    def test_to_load_record_drops_heavy_fields(self):
        record = to_load_record(_record(0))
        self.assertEqual(set(record), {"url", "title", "timestamp", "country", "degrees"})

    def test_sort_row_bundle_orders_by_natural_key_and_keeps_local_ids(self):
        bundle = {
            "universities": [{"id": 1, "name": "B Univ"}, {"id": 2, "name": "A Univ"}],
            "degree_programs": [
                {"id": 1, "university_id": 1, "source_url": "https://b/", "program_name": "X"},
                {"id": 2, "university_id": 2, "source_url": "https://a/", "program_name": "Y"},
            ],
            "tuition_patterns": [],
            "program_tuition_map": [{"degree_program_id": 2, "tuition_pattern_id": 1}],
        }
        result = sort_row_bundle(bundle)
        self.assertEqual([u["name"] for u in result["universities"]], ["A Univ", "B Univ"])
        self.assertEqual([p["id"] for p in result["degree_programs"]], [2, 1])
        self.assertEqual(result["degree_programs"][0]["university_id"], 2)


if __name__ == "__main__":
    unittest.main()
