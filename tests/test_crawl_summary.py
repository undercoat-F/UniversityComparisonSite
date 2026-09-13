import unittest

from ControlPlane.schedular import build_crawl_summary
from dataclass.dataclass import SiteState


class TestCrawlSummary(unittest.TestCase):
    def test_includes_total_and_per_domain_metrics(self):
        first_site = SiteState(domain="alpha.example", start_urls=[])
        first_site.log.update(success_count=3, error_count=1, total_time=2.345)
        first_site.visited_count_total = 4
        first_site.extracted_record_count_total = 2
        first_site.extracted_degree_count_total = 5

        second_site = SiteState(domain="beta.example", start_urls=[])
        second_site.log.update(success_count=1, error_count=0, total_time=0.5)
        second_site.visited_count_total = 1
        second_site.extracted_record_count_total = 1
        second_site.extracted_degree_count_total = 2

        summary = build_crawl_summary(
            [second_site, first_site],
            target_url_count=3,
            total_records=3,
            total_degrees=7,
            started_at="2026-08-19 10:00:00",
            finished_at="2026-08-19 10:00:04",
            elapsed_seconds=4.321,
        )

        self.assertEqual(summary["visited_url_count"], 5)
        self.assertEqual(summary["unique_explored_url_count"], 5)
        self.assertEqual(summary["successful_requests"], 4)
        self.assertEqual(summary["failed_requests"], 1)
        self.assertEqual(summary["extracted_records"], 3)
        self.assertEqual(summary["extracted_degrees"], 7)
        self.assertEqual(summary["elapsed_seconds"], 4.321)
        self.assertEqual(summary["domains"][0]["domain"], "alpha.example")
        self.assertEqual(summary["domains"][0]["request_elapsed_seconds"], 2.345)


if __name__ == "__main__":
    unittest.main(verbosity=2)