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

from ETL.dispatcher import MemoryPressureController


class MemoryPressureControllerTests(unittest.TestCase):
    def test_pauses_at_high_watermark(self):
        controller = MemoryPressureController(350, 300)

        paused, changed = controller.should_pause(350)

        self.assertTrue(paused)
        self.assertTrue(changed)

    def test_stays_paused_until_rss_reaches_resume_watermark(self):
        controller = MemoryPressureController(350, 300)
        controller.should_pause(360)

        paused, changed = controller.should_pause(301)
        self.assertTrue(paused)
        self.assertFalse(changed)

        paused, changed = controller.should_pause(300)
        self.assertFalse(paused)
        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()