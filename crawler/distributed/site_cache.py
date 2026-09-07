from __future__ import annotations

import os
from collections import OrderedDict
from typing import Callable

from dataclass.dataclass import SiteState

DOMAIN_CACHE_MAX_ENV = "WORKER_DOMAIN_CACHE_MAX_DOMAINS"
DOMAIN_CACHE_RELEASE_EVERY_ENV = "WORKER_DOMAIN_CACHE_RELEASE_EVERY"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


class DomainSiteCache:
    """workerプロセス内でSiteStateをドメイン単位に使い回すLRUキャッシュ。"""

    def __init__(self, max_domains: int | None = None, release_every: int | None = None) -> None:
        self._max_domains = max_domains if max_domains is not None else _env_int(DOMAIN_CACHE_MAX_ENV, 500)
        self._release_every = (
            release_every if release_every is not None else _env_int(DOMAIN_CACHE_RELEASE_EVERY_ENV, 200)
        )
        self._cache: OrderedDict[str, SiteState] = OrderedDict()
        self._task_counts: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._cache)

    def get_or_create(self, domain: str, factory: Callable[[], SiteState]) -> SiteState:
        if domain in self._cache:
            self._cache.move_to_end(domain)
            return self._cache[domain]

        site = factory()
        self._cache[domain] = site
        self._task_counts[domain] = 0
        if len(self._cache) > self._max_domains:
            evicted_domain, _ = self._cache.popitem(last=False)
            self._task_counts.pop(evicted_domain, None)
        return site

    def note_task_processed(self, domain: str) -> None:
        if domain not in self._cache:
            return
        count = self._task_counts.get(domain, 0) + 1
        self._task_counts[domain] = count
        if count % self._release_every == 0:
            site = self._cache[domain]
            site.memory_released = False
            site.release_runtime_memory()
