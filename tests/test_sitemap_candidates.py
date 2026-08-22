import unittest

from crawler.crawlworker import _select_likely_sitemap_candidates


class SitemapCandidateSelectionTests(unittest.TestCase):
    def test_caps_unique_candidates_without_building_a_full_candidate_list(self):
        urls = [
            "https://example.edu/programs/computer-science",
            "https://example.edu/programs/computer-science",
            "https://example.edu/courses/mathematics",
            "https://example.edu/study/physics",
        ]

        candidates = _select_likely_sitemap_candidates(urls, max_candidates=2)

        self.assertEqual(
            candidates,
            [
                "https://example.edu/programs/computer-science",
                "https://example.edu/courses/mathematics",
            ],
        )


if __name__ == "__main__":
    unittest.main()