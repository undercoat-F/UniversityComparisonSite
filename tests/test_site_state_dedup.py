#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import MagicMock

from dataclass.dataclass import QueueBudget, SiteState


class TestSiteStateDedupStore(unittest.TestCase):
    def test_enqueue_rejected_when_dedup_store_reports_already_seen(self):
        dedup_store = MagicMock()
        dedup_store.mark_seen.return_value = False
        site = SiteState(domain="example.edu", start_urls=[], max_depth=2, dedup_store=dedup_store)

        self.assertFalse(site.enqueue("https://example.edu/a", depth=0))
        dedup_store.mark_seen.assert_called_once_with("https://example.edu/a")

    def test_enqueue_releases_budget_when_dedup_store_rejects(self):
        budget = QueueBudget(limit=5)
        dedup_store = MagicMock()
        dedup_store.mark_seen.return_value = False
        site = SiteState(
            domain="example.edu", start_urls=[], max_depth=2, enqueue_budget=budget, dedup_store=dedup_store
        )

        self.assertFalse(site.enqueue("https://example.edu/a", depth=0))
        self.assertEqual(budget.pending_count, 0)

    def test_enqueue_accepted_when_dedup_store_reports_new_url(self):
        dedup_store = MagicMock()
        dedup_store.mark_seen.return_value = True
        site = SiteState(domain="example.edu", start_urls=[], max_depth=2, dedup_store=dedup_store)

        self.assertTrue(site.enqueue("https://example.edu/a", depth=0))
        self.assertEqual(len(site.queue), 1)

    def test_enqueue_fails_open_when_dedup_store_raises(self):
        dedup_store = MagicMock()
        dedup_store.mark_seen.side_effect = ConnectionError("redis down")
        site = SiteState(domain="example.edu", start_urls=[], max_depth=2, dedup_store=dedup_store)

        self.assertTrue(site.enqueue("https://example.edu/a", depth=0))
        self.assertEqual(len(site.errors), 1)


if __name__ == "__main__":
    unittest.main()
