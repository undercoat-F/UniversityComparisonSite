from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional
from urllib.robotparser import RobotFileParser
from enum import Enum, auto

if TYPE_CHECKING:
    from ETL.dedup_store import DedupStore


@dataclass
class QueueBudget:
    limit: int
    pending_count: int = 0

    def can_reserve(self) -> bool:
        return self.pending_count < self.limit

    def reserve(self) -> bool:
        if not self.can_reserve():
            return False
        self.pending_count += 1
        return True

    def release(self) -> None:
        if self.pending_count > 0:
            self.pending_count -= 1

@dataclass
class URLTask:
    url: str
    depth: int
    discovered_from: str = ""
    retry_count: int = 0
    status: str = "pending"
    queued_at: float = field(default_factory=time.time)


@dataclass
class CrawlAttempt:
    url: str
    ok: bool
    task_depth: int = 0
    status_code: Optional[int] = None
    used_fallback: bool = False
    error: str = ""
    discovered_urls: int = 0
    queued_urls: list[str] = field(default_factory=list)
    extracted_records: list[dict[str, Any]] = field(default_factory=list)
    connection_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FetchResult:
    html_text: str
    status_code: Optional[int]
    used_fallback: bool
    connection_log: list[dict[str, Any]]


@dataclass
class SiteState:
    domain: str
    start_urls: list[str]
    run_id: Optional[int] = None
    queue_logger: Optional[Any] = None
    record_sink: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None
    error_sink: Optional[Callable[[str, str], None]] = None
    error_buffer_limit: int = 200
    retain_extracted_records: bool = True
    enqueue_budget: Optional[QueueBudget] = None
    dedup_store: Optional["DedupStore"] = None
    crawl_delay: float = 0.0
    last_access: float = 0.0
    user_agent: str = "*"
    status: str = "active"
    max_depth: int = 1
    log: dict = field(
        default_factory=lambda: {
            "success_count": 0,
            "error_count": 0,
            "total_time": 0.0,
            "fallback_count": 0,
        }
    )
    sitemap: Optional[Any] = None
    domain_semaphore_limit: int = 1
    robotstxt: Optional[str] = None

    queue: deque[URLTask] = field(default_factory=deque)
    start_url_set: set[str] = field(default_factory=set)
    visited: set[str] = field(default_factory=set)
    queued: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    in_progress_urls: set[str] = field(default_factory=set)
    extracted_links_by_url: dict[str, list[str]] = field(default_factory=dict)
    crawl_attempts: list[CrawlAttempt] = field(default_factory=list)
    extracted_records: list[dict[str, Any]] = field(default_factory=list)
    extracted_record_count_total: int = 0
    extracted_degree_count_total: int = 0
    visited_count_total: int = 0
    root_visited_count: int = 0
    root_visited_urls: set[str] = field(default_factory=set)
    memory_released: bool = False
    tag_class_logged_urls: set[str] = field(default_factory=set)
    robots_parser: Optional[RobotFileParser] = None
    robots_ready: bool = False
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_candidates: list[str] = field(default_factory=list)
    sitemap_candidate_count: int = 0
    sitemap_attempted: bool = False

    extraction_drop_stats: dict[str, int] = field(
        default_factory=lambda: {
            "degree_line_hits": 0,
            "dropped_fee_context": 0,
            "dropped_non_numeric": 0,
            "kept_with_price": 0,
            "kept_without_price": 0,
        }
    )

    def __post_init__(self) -> None:
        self.start_url_set = set(self.start_urls)
        for url in self.start_urls:
            self.enqueue(url=url, depth=0)

    def post_init(self) -> None:
        self.__post_init__()

    @property
    def success_count(self) -> int:
        return int(self.log["success_count"])

    @property
    def error_count(self) -> int:
        return int(self.log["error_count"])

    @property
    def total_time(self) -> float:
        return float(self.log["total_time"])

    @property
    def fallback_count(self) -> int:
        return int(self.log["fallback_count"])

    def has_pending(self) -> bool:
        return bool(self.queue)

    def can_fetch(self) -> bool:
        return (time.time() - self.last_access) >= self.crawl_delay

    def seconds_until_ready(self) -> float:
        return max(0.0, self.crawl_delay - (time.time() - self.last_access))

    def mark_access(self) -> None:
        self.last_access = time.time()

    def enqueue(self, url: str, depth: int, discovered_from: str = "") -> bool:
        if url in self.visited or url in self.queued or depth > self.max_depth:
            return False
        if self.enqueue_budget is not None and not self.enqueue_budget.reserve():
            return False
        if self.dedup_store is not None and not self._mark_seen_in_dedup_store(url):
            if self.enqueue_budget is not None:
                self.enqueue_budget.release()
            return False
        self.queue.append(URLTask(url=url, depth=depth, discovered_from=discovered_from))
        self.queued.add(url)

        if self.queue_logger is not None and self.run_id is not None:
            try:
                source = "link"
                parent_for_log = discovered_from
                if discovered_from == "sitemap":
                    source = "sitemap"
                    parent_for_log = ""
                elif discovered_from == "manual":
                    source = "manual"
                    parent_for_log = ""

                self.queue_logger.upsert_queue_state(
                    run_id=self.run_id,
                    url=url,
                    parent_url=parent_for_log,
                    domain=self.domain,
                    depth=depth,
                    status="pending",
                    discovered_from=discovered_from,
                )
                if parent_for_log:
                    self.queue_logger.add_edge(
                        run_id=self.run_id,
                        parent_url=parent_for_log,
                        child_url=url,
                        parent_domain=self.domain,
                        child_domain=self.domain,
                        depth=depth,
                        source=source,
                    )
            except Exception:
                pass

        return True

    def _mark_seen_in_dedup_store(self, url: str) -> bool:
        """Redis障害時はcrawlを止めないよう、未訪問扱い(True)にフェイルオープンする。"""
        try:
            return self.dedup_store.mark_seen(url)
        except Exception as exc:  # noqa: BLE001
            self.add_error(f"dedup_store_unavailable domain={self.domain}: {type(exc).__name__}: {exc}")
            return True

    def pop_next_task(self) -> Optional[URLTask]:
        if not self.queue:
            return None
        task = self.queue.popleft()
        self.queued.discard(task.url)
        if self.enqueue_budget is not None:
            self.enqueue_budget.release()
        return task

    def set_domain_semaphore_limit(self, limit: int) -> None:
        self.domain_semaphore_limit = limit

    def start_task(self, task: URLTask) -> None:
        self.mark_access()
        if task.url not in self.visited:
            self.visited.add(task.url)
            self.visited_count_total += 1
            if task.url in self.start_url_set and task.url not in self.root_visited_urls:
                self.root_visited_urls.add(task.url)
                self.root_visited_count += 1
        self.in_progress_urls.add(task.url)

    def finish_task(self, task_url: str) -> None:
        self.in_progress_urls.discard(task_url)

    def record_links(self, source_url: str, links: list[str]) -> None:
        return

    def record_attempt(self, attempt: CrawlAttempt, elapsed_sec: float) -> None:
        self.log["total_time"] += elapsed_sec
        if attempt.ok:
            self.log["success_count"] += 1
        else:
            self.log["error_count"] += 1
        if attempt.used_fallback:
            self.log["fallback_count"] += 1

    def release_runtime_memory(self) -> None:
        if self.memory_released:
            return
        self.visited.clear()
        self.extracted_links_by_url.clear()
        self.crawl_attempts.clear()
        self.tag_class_logged_urls.clear()
        self.memory_released = True

    async def add_extracted_record(self, record: dict[str, Any]) -> None:
        self.extracted_record_count_total += 1
        degrees = record.get("degrees", [])
        if isinstance(degrees, list):
            self.extracted_degree_count_total += len(degrees)
        if self.record_sink is not None:
            await self.record_sink(self.domain, record)
        if self.retain_extracted_records:
            self.extracted_records.append(record)

    def add_error(self, error_msg: str) -> None:
        if self.error_sink is not None:
            self.error_sink(self.domain, error_msg)
        self.errors.append(error_msg)
        if self.error_buffer_limit > 0 and len(self.errors) > self.error_buffer_limit:
            overflow = len(self.errors) - self.error_buffer_limit
            del self.errors[:overflow]

@dataclass
class SeedDiscovery:
    domain_url: str
    seed_urls: list[str] = field(default_factory=list)
    depth: Optional[int] = 2

    seed_candidates: dict[str, float] = field(default_factory=dict)

    source_site: str | None = None
    university_name: str | None = None
    requests_log: list[dict[str, Any]] = field(default_factory=list)

    def add_log(
        self,
        url: str,
        status_code: Optional[int],
        error: Optional[str] = None,
        step: Literal["observe", "search", "extract", "transform", "add"] = "observe",
    ) -> None:
        self.requests_log.append({
            "url": url,
            "status_code": status_code,
            "error": error,
            "timestamp": time.time(),
            "step": step  # This can be set based on the context of the log entry
        })

    def add_seed_candidate(self, url: str, score: float) -> None:
        current = self.seed_candidates.get(url)
        if current is None or score > current:
            self.seed_candidates[url] = score


@dataclass
class SearchRequest:
    source_url: str
    source_domain: str
    university_names: list[str]
    content_type: str
    candidate_lines: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    query: str
    url: str
    title: str
    snippet: str
    score: float
    is_course_like: bool
    search_form_detected: bool = False
    course_list_detected: bool = False


@dataclass
class SearchResult:
    source_url: str
    source_domain: str
    university_names: list[str]
    hits: list[SearchHit]
    root_seed_urls: list[str]
    detailed_seed_urls: list[str]
    course_list_found: bool
    recommended_depth: int
    duplicate_root_urls: list[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class SeedTransformInput(SearchResult):
    api_type: str = "brave"
    first_search_count: int = 0
    internal_link_extracted_count: int = 0
    fallback_executed: bool = False
    api_usage_count: int = 0
    search_queries: list[str] = field(default_factory=list)
    run_id: Optional[int] = None
    source_stage: str = "seed_searcher"

# 監視ページの特徴を表すクラス
class ContentType(Enum):
    PDF = "pdf"
    HTML = "html"
    IMAGE = "image"
    JSON = "json"
    OTHER = "other"

@dataclass #監視サイトの分析結果を保持するクラス
class PageAnalysis:
    content_type: ContentType = ContentType.OTHER

    table_list: bool = False#TABLEタグページ,表形式PDF
    search_form: bool = False#大学検索フォーム
    profile: bool = False#大学検索詳細
    text: bool = False#テキスト中心のページ,テキスト形式PDF

    candidate_lines: list[str] = field(default_factory=list)
    #タグ集計結果を持つ辞書を追加してもよい
    extracted_universitynamelist: list[str] = field(default_factory=list)

    response : Optional[Any] = None

@dataclass
class SearchRunLogRecord:
    source_url: str
    source_domain: str
    api_type: str
    first_search_count: int
    internal_link_extracted_count: int
    fallback_executed: bool
    api_usage_count: int
    run_id: Optional[int] = None
    source_stage: str = "seed_searcher"