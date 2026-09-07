#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from dataclass.dataclass import SiteState
from crawler.distributed.site_cache import DomainSiteCache


def _make_site(domain: str) -> SiteState:
    return SiteState(domain=domain, start_urls=[], max_depth=2)


class TestDomainSiteCache(unittest.TestCase):
    def test_get_or_create_reuses_same_instance_for_same_domain(self):
        cache = DomainSiteCache(max_domains=10, release_every=1000)

        site_a = cache.get_or_create("example.edu", lambda: _make_site("example.edu"))
        site_b = cache.get_or_create("example.edu", lambda: _make_site("example.edu"))

        self.assertIs(site_a, site_b)

    def test_evicts_least_recently_used_domain_when_over_capacity(self):
        cache = DomainSiteCache(max_domains=2, release_every=1000)

        cache.get_or_create("a.edu", lambda: _make_site("a.edu"))
        cache.get_or_create("b.edu", lambda: _make_site("b.edu"))
        cache.get_or_create("c.edu", lambda: _make_site("c.edu"))

        self.assertEqual(len(cache), 2)
        # a.eduが最初に追加され、その後アクセスされていないので追い出される
        created_domains = []
        cache.get_or_create("a.edu", lambda: (created_domains.append("a.edu") or _make_site("a.edu")))
        self.assertEqual(created_domains, ["a.edu"])  # 追い出されていたので再生成された

    def test_recently_used_domain_survives_eviction(self):
        cache = DomainSiteCache(max_domains=2, release_every=1000)

        cache.get_or_create("a.edu", lambda: _make_site("a.edu"))
        cache.get_or_create("b.edu", lambda: _make_site("b.edu"))
        cache.get_or_create("a.edu", lambda: _make_site("a.edu"))  # aを再アクセスして最新化
        cache.get_or_create("c.edu", lambda: _make_site("c.edu"))  # bが追い出されるはず

        created_domains = []
        cache.get_or_create("a.edu", lambda: (created_domains.append("a.edu") or _make_site("a.edu")))
        self.assertEqual(created_domains, [])  # aは生き残っている

    def test_note_task_processed_releases_runtime_memory_periodically(self):
        cache = DomainSiteCache(max_domains=10, release_every=3)
        site = cache.get_or_create("example.edu", lambda: _make_site("example.edu"))
        site.visited.add("https://example.edu/a")

        cache.note_task_processed("example.edu")
        cache.note_task_processed("example.edu")
        self.assertIn("https://example.edu/a", site.visited)

        cache.note_task_processed("example.edu")  # 3件目でリセットされる
        self.assertNotIn("https://example.edu/a", site.visited)

    def test_note_task_processed_resets_repeatedly_not_just_once(self):
        cache = DomainSiteCache(max_domains=10, release_every=2)
        site = cache.get_or_create("example.edu", lambda: _make_site("example.edu"))

        cache.note_task_processed("example.edu")
        site.visited.add("https://example.edu/a")
        cache.note_task_processed("example.edu")  # 2件目でリセット
        self.assertNotIn("https://example.edu/a", site.visited)

        site.visited.add("https://example.edu/b")
        cache.note_task_processed("example.edu")
        cache.note_task_processed("example.edu")  # 4件目で再度リセットされるべき
        self.assertNotIn("https://example.edu/b", site.visited)


if __name__ == "__main__":
    unittest.main()
