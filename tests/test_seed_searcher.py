#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_seed_searcher.py
seed_searcher のユニットテスト
- 検索API呼び出し
- 公式ページからの内部リンク抽出
- コース未検出時の fallback 再検索
ネット通信なし・DB不要。
"""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.SimpleNamespace(Error=Exception)
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

from dataclass.dataclass import ContentType, PageAnalysis, SearchHit
from observer.observe_supervisor import ObserveStackItem
from observer import seed_searcher


class _DummyResponse:
    def __init__(self, text: str, content_type: str = "text/html", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _build_item(university_name: str = "Test University") -> ObserveStackItem:
    page = PageAnalysis(content_type=ContentType.HTML)
    page.extracted_universitynamelist = [university_name]
    return ObserveStackItem(
        source_url="https://observer.example.org/list",
        page_analysis=page,
        university_names=[university_name],
        request_logs=[],
    )


async def _passthrough_probe_hits(hits, errors):
    return hits


class TestSeedSearcher(unittest.TestCase):
    def test_discovery_queries_allow_more_than_six_by_default(self):
        req = seed_searcher.SearchRequest(
            source_url="https://example.org",
            source_domain="example.org",
            university_names=["A University", "B University", "C University"],
            content_type="html",
            candidate_lines=[],
        )

        queries = seed_searcher._build_domain_discovery_queries(req)
        self.assertGreaterEqual(len(queries), 7)

    def test_discovery_queries_are_distributed_across_universities_under_global_limit(self):
        req = seed_searcher.SearchRequest(
            source_url="https://example.org",
            source_domain="example.org",
            university_names=["A University", "B University", "C University"],
            content_type="html",
            candidate_lines=[],
        )

        with patch.dict(
            "os.environ",
            {
                "SEARCH_DISCOVERY_QUERY_LIMIT": "4",
                "SEARCH_DISCOVERY_PER_UNIVERSITY_LIMIT": "3",
            },
            clear=False,
        ):
            queries = seed_searcher._build_domain_discovery_queries(req)

        self.assertEqual(len(queries), 4)
        # 英語名は official を先に使う（1巡目で各大学の1件目、2巡目で A の2件目）
        self.assertIn("A University official", queries)
        self.assertIn("B University official", queries)
        self.assertIn("C University official", queries)
        self.assertIn("A University official site", queries)

    def test_japanese_names_use_koushiki_first(self):
        req = types.SimpleNamespace(university_names=["東京大学", "University of Otago"], source_domain="")
        with patch.dict("os.environ", {"SEARCH_DISCOVERY_PER_UNIVERSITY_LIMIT": "1"}, clear=False):
            queries = seed_searcher._build_domain_discovery_queries(req)
        self.assertEqual(queries, ["東京大学 公式", "University of Otago official"])

    def test_query_generation_summary_is_logged(self):
        item = _build_item("Summary University")

        def fake_search(query: str, num_results: int = 10):
            return []

        with (
            patch.dict(
                "os.environ",
                {
                    "BRAVE_API_KEY": "dummy-key",
                    "SEARCH_DISCOVERY_QUERY_LIMIT": "2",
                    "SEARCH_DISCOVERY_PER_UNIVERSITY_LIMIT": "3",
                },
                clear=False,
            ),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search),
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(text="<html><body>ok</body></html>"),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        summary = next((e for e in result.errors if e.startswith("query_generation:")), "")
        self.assertIn("generated=3", summary)
        self.assertIn("selected=2", summary)
        self.assertIn("dropped_global=1", summary)

    def test_official_domain_selection_falls_back_when_strict_empty(self):
        hits = [
            SearchHit(
                query="東京大学 公式",
                url="https://www.u-tokyo.ac.jp/",
                title="東京大学",
                snippet="公式サイト",
                score=8.0,
                is_course_like=True,
            )
        ]

        domains = seed_searcher._select_official_domains(hits, ["東京大学"])
        self.assertIn("www.u-tokyo.ac.jp", domains)

    def test_query_uses_normalized_university_name_from_sentence(self):
        item = _build_item("私は東京大学に行って研究をしています")

        call_queries = []

        def fake_search(query: str, num_results: int = 10):
            call_queries.append(query)
            if query == "東京大学 公式":
                return [
                    {
                        "url": "https://www.u-tokyo.ac.jp/",
                        "title": "東京大学",
                        "snippet": "公式サイト",
                    }
                ]
            return []

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search),
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(text="<html><body>ok</body></html>"),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        self.assertIn("東京大学 公式", call_queries)
        self.assertTrue(any("u-tokyo.ac.jp" in u for u in result.root_seed_urls))

    def test_non_official_domains_can_be_included_in_seed_urls(self):
        item = _build_item("Official University")

        def fake_search(query: str, num_results: int = 10):
            if query in {"Official University 公式", "Official University official"}:
                return [
                    {
                        "url": "https://www.official.ac.uk/",
                        "title": "Official University",
                        "snippet": "Official site",
                    },
                    {
                        "url": "https://www.traininginstitute.org/courses/official-university",
                        "title": "Official University courses",
                        "snippet": "course list",
                    },
                ]
            return []

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search),
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(text="<html><body>ok</body></html>"),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        self.assertTrue(any("official.ac.uk" in u for u in result.root_seed_urls))
        self.assertTrue(any("traininginstitute.org" in u for u in result.root_seed_urls))
        self.assertTrue(any("traininginstitute.org" in u for u in result.detailed_seed_urls))

    def test_blocked_noise_domains_are_not_added_to_seed_urls(self):
        item = _build_item("Noise University")

        def fake_search(query: str, num_results: int = 10):
            if query in {"Noise University 公式", "Noise University official"}:
                return [
                    {
                        "url": "https://www.noise.ac.uk/",
                        "title": "Noise University Official Site",
                        "snippet": "Official",
                    },
                    {
                        "url": "https://www.zoominfo.com/c/noise-university-courses/123",
                        "title": "Noise University Courses",
                        "snippet": "courses",
                    },
                ]
            return []

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search),
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(text="<html><body>ok</body></html>"),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        self.assertTrue(any("noise.ac.uk" in u for u in result.root_seed_urls))
        self.assertFalse(any("zoominfo.com" in u for u in result.root_seed_urls))
        self.assertFalse(any("zoominfo.com" in u for u in result.detailed_seed_urls))

    def test_sitemap_discovery_adds_course_candidates(self):
        item = _build_item("Sitemap University")

        def fake_search(query: str, num_results: int = 10):
            if query in {"Sitemap University 公式", "Sitemap University official"}:
                return [
                    {
                        "url": "https://www.sitemap.ac.uk/",
                        "title": "Sitemap University Official Site",
                        "snippet": "Official",
                    }
                ]
            return []

        def fake_requests_get(url, headers=None, timeout=None, allow_redirects=True):
            if url == "https://www.sitemap.ac.uk/robots.txt":
                return _DummyResponse("Sitemap: https://www.sitemap.ac.uk/sitemap.xml", content_type="text/plain")
            if url == "https://www.sitemap.ac.uk/sitemap.xml":
                return _DummyResponse(
                    """
                    <urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
                      <url><loc>https://www.sitemap.ac.uk/courses/undergraduate</loc></url>
                      <url><loc>https://www.sitemap.ac.uk/programmes/postgraduate</loc></url>
                    </urlset>
                    """,
                    content_type="application/xml",
                )
            return _DummyResponse("<html><body>ok</body></html>")

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search),
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch("observer.seed_searcher.requests.get", side_effect=fake_requests_get),
        ):
            result = seed_searcher.search_seeds(item)

        self.assertTrue(any("/courses/undergraduate" in url for url in result.detailed_seed_urls))
        self.assertTrue(any(hit.query == "sitemap" for hit in result.hits))

    def test_search_is_skipped_when_cached_seed_urls_already_exist(self):
        item = _build_item("Cached University")

        cached_result = seed_searcher.SearchResult(
            source_url=item.source_url,
            source_domain="observer.example.org",
            university_names=["Cached University"],
            hits=[],
            root_seed_urls=[],
            detailed_seed_urls=[],
            course_list_found=True,
            recommended_depth=1,
            duplicate_root_urls=["https://www.cached.ac.uk"],
            errors=["search_skipped_cached_seed_urls: source_url=https://observer.example.org/list cached_roots=1"],
        )

        with (
            patch("observer.seed_searcher._load_cached_seed_result", return_value=cached_result),
            patch("observer.seed_searcher.BraveSearchAPI.search") as m_brave_search,
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
        ):
            result = seed_searcher.search_seeds(item)

        m_brave_search.assert_not_called()
        self.assertEqual(result.api_type, "cache-seed-urls")
        self.assertEqual(result.api_usage_count, 0)
        self.assertTrue(any("search_skipped_cached_seed_urls:" in err for err in result.errors))

    def test_search_and_internal_link_exploration(self):
        item = _build_item("Test University")

        def fake_search(query: str, num_results: int = 10):
            if query in {"Test University 公式", "Test University official"}:
                return [
                    {
                        "url": "https://www.testuniversity.ac.uk/",
                        "title": "Test University Official Site",
                        "snippet": "Welcome",
                    }
                ]
            return []

        async def fake_probe_urls_async(urls, errors):
            # 内部リンク探索で見つかった URL を course-like として返す
            return [
                SearchHit(
                    query="internal-link",
                    url="https://www.testuniversity.ac.uk/courses",
                    title="Courses",
                    snippet="Undergraduate and postgraduate courses",
                    score=10.0,
                    is_course_like=True,
                    course_list_detected=True,
                )
            ]

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search) as m_search,
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch("observer.seed_searcher._probe_urls_async", new=fake_probe_urls_async),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(
                    text='<html><body><a href="/courses">Courses</a></body></html>'
                ),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        self.assertGreaterEqual(m_search.call_count, 2)  # 第1検索は大学名 + 公式/official
        self.assertTrue(result.course_list_found)
        self.assertEqual(result.recommended_depth, 1)
        self.assertTrue(any("/courses" in url for url in result.detailed_seed_urls))

    def test_fallback_search_runs_when_course_not_found(self):
        item = _build_item("Fallback University")

        call_queries = []

        def fake_search(query: str, num_results: int = 10):
            call_queries.append(query)
            if query in {"Fallback University 公式", "Fallback University official"}:
                return [
                    {
                        "url": "https://www.fallback.ac.uk/",
                        "title": "Fallback University Official Site",
                        "snippet": "Welcome",
                    }
                ]
            if query == "Fallback University official site":
                return []
            if query == "site:www.fallback.ac.uk courses":
                return []
            if query == "site:www.fallback.ac.uk programmes":
                return [
                    {
                        "url": "https://www.fallback.ac.uk/programmes",
                        "title": "Programmes",
                        "snippet": "Find your programme",
                    }
                ]
            return []

        with (
            patch.dict("os.environ", {"BRAVE_API_KEY": "dummy-key"}, clear=False),
            patch("observer.seed_searcher.BraveSearchAPI.search", side_effect=fake_search) as m_search,
            patch("observer.seed_searcher._existing_root_urls", return_value=set()),
            patch("observer.seed_searcher.SearchLogStore.from_env", return_value=None),
            patch("observer.seed_searcher._probe_hits_async", new=_passthrough_probe_hits),
            patch(
                "observer.seed_searcher.requests.get",
                return_value=_DummyResponse(text="<html><body><a href='/about'>About</a></body></html>"),
            ),
        ):
            result = seed_searcher.search_seeds(item)

        # fallback クエリまで呼ばれていることを確認
        self.assertGreaterEqual(m_search.call_count, 4)
        self.assertIn("site:www.fallback.ac.uk courses", call_queries)
        self.assertIn("site:www.fallback.ac.uk programmes", call_queries)
        self.assertTrue(result.course_list_found)
        self.assertEqual(result.recommended_depth, 1)
        self.assertTrue(any("/programmes" in hit.url for hit in result.hits))

if __name__ == "__main__":
    unittest.main(verbosity=2)
