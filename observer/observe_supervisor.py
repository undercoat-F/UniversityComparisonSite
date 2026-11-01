from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from dataclass.dataclass import PageAnalysis
from observer import seed_observer


# 大学名を抜き出す一覧ページ。政府・政府機関の名簿を基本とし、件数を増やしたい国は Wikipedia の一覧を足す。
# 条件: 大学名が HTML（表・リンク・リスト）か PDF にそのまま書かれていること（JavaScript で表示するページは読めない）。
# bot 対策で 403 を返すサイト（Complete University Guide、Universities Canada など）は使わない。
OBSERVE_SOURCE_URLS: list[str] = [
    # 日本: 文部科学省 数理・データサイエンス・AI 教育プログラム認定校
    "https://www.mext.go.jp/a_menu/koutou/suuri_datascience_ai/mext_00005.html",
    # カナダ: CICIC（カナダ政府が支援する認定機関の名簿）
    "https://www.cicic.ca/869/resultats.canada?search=",
    "https://en.wikipedia.org/wiki/List_of_universities_in_Canada",
    # イギリス: GOV.UK 学位を出せる機関（recognised bodies）
    "https://www.gov.uk/check-university-award-degree/recognised-bodies",
    "https://en.wikipedia.org/wiki/List_of_universities_in_the_United_Kingdom",
    # オーストラリア: Study Australia（豪政府）
    "https://www.studyaustralia.gov.au/en/plan-your-studies/list-of-australian-universities",
    # ニュージーランド: Study with New Zealand（NZ 政府機関）
    "https://www.studywithnewzealand.govt.nz/en/study-options/universities",
    # アイルランド: HEA（高等教育庁）。gov.ie/en/schools は小中学校の一覧のため外した
    "https://hea.ie/higher-education-institutions/",
]


@dataclass
class ObserveRunResult:
    url: str
    added_log_count: int
    error_count: int
    stacked_for_searcher: bool


@dataclass
class ObserveStackItem:
    source_url: str
    page_analysis: PageAnalysis
    university_names: list[str]
    request_logs: list[dict[str, Any]]
    observe_run_id: int | None = None


class InMemoryObserveQueue:
    def __init__(self) -> None:
        self._items: deque[ObserveStackItem] = deque()

    def push(self, item: ObserveStackItem) -> None:
        self._items.append(item)

    def pop(self) -> Optional[ObserveStackItem]:
        if not self._items:
            return None
        return self._items.popleft()

    def __len__(self) -> int:
        return len(self._items)

    def to_list(self) -> list[ObserveStackItem]:
        return list(self._items)


def _slice_new_logs(before_len: int) -> list[dict[str, Any]]:
    logs = seed_observer.Seed_State.requests_log
    return logs[before_len:]


def run_supervisor(
    source_urls: list[str] | None = None,
    stack_queue: InMemoryObserveQueue | None = None,
    observe_run_id: int | None = None,
) -> tuple[list[ObserveRunResult], InMemoryObserveQueue]:
    urls = source_urls or OBSERVE_SOURCE_URLS
    queue = stack_queue or InMemoryObserveQueue()
    results: list[ObserveRunResult] = []

    for url in urls:
        before_len = len(seed_observer.Seed_State.requests_log)
        page_analysis = seed_observer.observe_url(url)
        new_logs = _slice_new_logs(before_len)
        error_count = sum(1 for item in new_logs if item.get("error"))
        stacked_for_searcher = False

        if page_analysis is not None:
            queue.push(
                ObserveStackItem(
                    source_url=url,
                    page_analysis=page_analysis,
                    university_names=list(page_analysis.extracted_universitynamelist),
                    request_logs=new_logs,
                    observe_run_id=observe_run_id,
                )
            )
            stacked_for_searcher = True

        results.append(
            ObserveRunResult(
                url=url,
                added_log_count=len(new_logs),
                error_count=error_count,
                stacked_for_searcher=stacked_for_searcher,
            )
        )

    return results, queue


def dispatch_to_searcher(
    queue: InMemoryObserveQueue,
    searcher_handler: Callable[[ObserveStackItem], None],
) -> int:
    dispatched = 0
    while True:
        item = queue.pop()
        if item is None:
            break
        searcher_handler(item)
        dispatched += 1
    return dispatched


def main() -> None:
    results, queue = run_supervisor()
    for result in results:
        print(
            "[OBSERVE_SUPERVISOR] "
            f"url={result.url} "
            f"added_logs={result.added_log_count} "
            f"errors={result.error_count} "
            f"stacked={result.stacked_for_searcher}"
        )
    print(f"[OBSERVE_SUPERVISOR] stacked_queue_size={len(queue)}")


if __name__ == "__main__":
    main()
