from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
import psycopg2
import requests
from dotenv import load_dotenv
#from playwright.sync_api import sync_playwright

from dataclass.dataclass import SearchHit, SearchRequest, SearchResult, SeedDiscovery, SeedTransformInput
from db.schema_config import get_observer_schema, get_public_schema, get_table_ref, set_search_path
from observer.observe_supervisor import ObserveStackItem
from observer.search_log import SearchLogStore, SearchRunLogRecord

load_dotenv(encoding="utf-8-sig")

SEED_URLS_TABLE = get_table_ref("SEED_URLS_TABLE")
SEED_OBSERVE_RESULTS_TABLE = get_table_ref("SEED_OBSERVE_RESULTS_TABLE")

COURSE_HINT_KEYWORDS = (
    "course",
    "courses",
    "program",
    "programmes",
    "degree",
    "degrees",
    "undergraduate",
    "postgraduate",
    "study",
    "academics",
)

NEGATIVE_HINT_KEYWORDS = (
    "news",
    "event",
    "blog",
    "ranking",
    "rankings",
    "wikipedia",
    "linkedin",
    "facebook",
    "x.com",
    "twitter",
)

BLOCKED_SEED_DOMAINS = {
    "wikipedia.org",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "zoominfo.com",
    "tiktok.com",
    "kotobank.jp",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_PROBE_URLS = 12
PROBE_CONCURRENCY = 6
MAX_INTERNAL_LINKS_PER_DOMAIN = 16
MAX_FALLBACK_QUERIES = 2
MAX_SITEMAP_URLS_PER_DOMAIN = 30
MAX_SITEMAP_RECURSION = 3
DEFAULT_MAX_DISCOVERY_QUERIES = 24
DEFAULT_DISCOVERY_PER_UNIVERSITY_LIMIT = 3
GOOGLE_RESULTS_EXTRACT_SCRIPT = (
    Path(__file__).with_name("google_extract_results.js").read_text(encoding="utf-8")
)


def _is_valid_absolute_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    if "*" in parsed.netloc:
        return False
    return True


def _json_to_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(v) for v in parsed if v]
    return []


def _get_observe_log_dsn() -> str:
    value = os.getenv("OBSERVE_LOG_POSTGRES_DSN", "").strip() or os.getenv("PARENT_DB_OWNER_CONNECTION", "").strip()
    if value:
        return value

    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if all((os.getenv(name, "").strip() for name in required)):
        host = os.getenv("DB_HOST", "").strip()
        dbname = os.getenv("DB_NAME", "").strip()
        user = os.getenv("DB_USER", "").strip()
        password = os.getenv("DB_PASSWORD", "").strip()
        port = os.getenv("DB_PORT", "5432").strip() or "5432"
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode=require"
    return ""

class BraveSearchAPI:
    """Brave Search API の薄いラッパー。"""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("BRAVE_API_KEY")
        self.api_type = "brave"
        self._client_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key or "",
        }

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 10) -> list[dict[str, str]]:
        if not self.enabled:
            return []

        url = "https://api.search.brave.com/res/v1/web/search"
        params = {"q": query, "count": num_results}

        response = httpx.get(url, params=params, headers=self._client_headers, timeout=12)
        response.raise_for_status()
        return self._extract_results(response.json())

    @staticmethod
    def _extract_results(payload: dict[str, Any]) -> list[dict[str, str]]:
        web = payload.get("web") or {}
        raw_results = web.get("results") or []
        results: list[dict[str, str]] = []
        for item in raw_results:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                {
                    "url": url,
                    "title": str(item.get("title") or "").strip(),
                    "snippet": str(item.get("description") or "").strip(),
                }
            )
        return results


# class PlaywrightFallbackSearch:
#     """Google 検索を Playwright で実行するフォールバック。"""

#     def __init__(self) -> None:
#         self.api_type = "playwright-google"
#         self._enabled = os.getenv("PLAYWRIGHT_GOOGLE_FALLBACK", "1").strip().lower() not in {
#             "0",
#             "false",
#             "off",
#             "no",
#         }
#         self._headless = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() not in {
#             "0",
#             "false",
#             "off",
#             "no",
#         }

#     @property
#     def enabled(self) -> bool:
#         return self._enabled

#     @staticmethod
#     def _normalize_google_href(href: str) -> str:
#         normalized = (href or "").strip()
#         if not normalized:
#             return ""
#         if normalized.startswith("/url?"):
#             parsed = urlparse(normalized)
#             q = parse_qs(parsed.query).get("q", [""])[0]
#             return q.strip()
#         return normalized

#     def search(self, query: str, num_results: int = 10) -> list[dict[str, str]]:
#         if not self.enabled:
#             return []

#         google_form = (
#             "https://www.google.com/search"
#             f"?q={quote_plus(query)}&num={max(1, min(int(num_results), 10))}&hl=en"
#         )

#         with sync_playwright() as playwright:
#             browser = playwright.chromium.launch(headless=self._headless)
#             context = browser.new_context(
#                 user_agent=DEFAULT_HEADERS["User-Agent"],
#                 locale="en-US",
#             )
#             page = context.new_page()
#             page.goto(google_form, wait_until="domcontentloaded", timeout=20000)

#             # 同意ダイアログが表示されるケースを許容しつつ検索結果抽出を試みる。
#             for selector in (
#                 'button:has-text("I agree")',
#                 'button:has-text("Accept all")',
#                 'button:has-text("同意する")',
#                 'button:has-text("すべて受け入れる")',
#             ):
#                 try:
#                     if page.locator(selector).count() > 0:
#                         page.locator(selector).first.click(timeout=800)
#                         break
#                 except Exception:
#                     pass

#             try:
#                 page.wait_for_selector("div#search", timeout=5000)
#             except Exception:
#                 pass

#             raw_items = page.evaluate(GOOGLE_RESULTS_EXTRACT_SCRIPT)
#             context.close()
#             browser.close()

#         rows: list[dict[str, str]] = []
#         seen_urls: set[str] = set()
#         for item in raw_items or []:
#             href = self._normalize_google_href(str(item.get("url") or ""))
#             title = str(item.get("title") or "").strip()
#             snippet = str(item.get("snippet") or "").strip()
#             if not href or not title:
#                 continue
#             if not _is_valid_absolute_http_url(href):
#                 continue
#             if _is_blocked_seed_domain(_domain(href)):
#                 continue
#             if href in seen_urls:
#                 continue
#             seen_urls.add(href)
#             rows.append({"url": href, "title": title, "snippet": snippet})
#             if len(rows) >= num_results:
#                 break
#         return rows


def _search_with_provider_fallback(
    *,
    query: str,
    num_results: int,
    primary: BraveSearchAPI,
    #secondary: PlaywrightFallbackSearch,
    errors: list[str],
) -> tuple[list[dict[str, str]], str | None, bool]:
    primary_error: Exception | None = None

    def _format_provider_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{type(exc).__name__}(status={exc.response.status_code})"
        if isinstance(exc, requests.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                return f"{type(exc).__name__}(status={status})"
        message = str(exc).strip().replace("\n", " ")
        if message:
            message = message.split("For more information check:", 1)[0].strip()
            if len(message) > 120:
                message = f"{message[:117]}..."
            return f"{type(exc).__name__}({message})"
        return type(exc).__name__

    if primary.enabled:
        try:
            rows = primary.search(query=query, num_results=num_results)
            return rows, primary.api_type, False
        except Exception as exc:  # noqa: BLE001
            primary_error = exc

    #if not secondary.enabled:
    #    if primary_error is not None:
    #        errors.append(
    #            f"query={query}: brave_failed={_format_provider_error(primary_error)} "
    #            "playwright_disabled all_search_providers_failed"
    #        )
    #    return [], None, bool(primary_error) or (not primary.enabled)

    #try:
    #    rows = secondary.search(query=query, num_results=num_results)
    #    reason = "brave_failed" if primary_error is not None else "brave_disabled"
    #    if primary_error is not None:
    #        errors.append(
    #            f"query={query}: brave_failed={_format_provider_error(primary_error)} "
    #            f"provider_fallback_used=playwright-google reason={reason} hits={len(rows)}"
    #        )
    #    else:
    #        errors.append(
    #            f"query={query}: brave_disabled provider_fallback_used=playwright-google "
    #            f"reason={reason} hits={len(rows)}"
    #        )
    #    return rows, secondary.api_type, True
    #except Exception as exc:  # noqa: BLE001
    #    primary_status = (
    #        f"brave_failed={_format_provider_error(primary_error)}"
    #        if primary_error is not None
    #        else "brave_disabled"
    #    )
    #    errors.append(
    #        f"query={query}: {primary_status} "
    #        f"playwright_failed={_format_provider_error(exc)} all_search_providers_failed"
    #    )
    #    return [], None, bool(primary_error) or (not primary.enabled)


def _resolve_api_type(
    primary_api_type: str,
    #fallback_api_type: str,
    used_provider_types: set[str],
) -> str:
    if not used_provider_types:
        return primary_api_type
    if used_provider_types == {primary_api_type}:
        return primary_api_type
    #if used_provider_types == {fallback_api_type}:
    #    return fallback_api_type
    #return f"{primary_api_type}+{fallback_api_type}"
    if len(used_provider_types) == 1:
        return next(iter(used_provider_types))
    return "+".join(sorted(used_provider_types))


def _get_db_params() -> dict[str, Any] | None:
    required_keys = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT"]
    if any(not os.getenv(key) for key in required_keys):
        return None
    return {
        "host": os.getenv("DB_HOST"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": int(os.getenv("DB_PORT", "5432")),
    }


def _root_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}"


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_blocked_seed_domain(domain: str) -> bool:
    d = (domain or "").strip().lower()
    if not d:
        return False
    for blocked in BLOCKED_SEED_DOMAINS:
        if d == blocked or d.endswith(f".{blocked}"):
            return True
    return False


def _course_like_score(url: str, title: str, snippet: str, query: str) -> float:
    combined = f"{url} {title} {snippet}".lower()
    path = (urlparse(url).path or "").lower()
    domain = _domain(url)
    score = 0.0

    if any(keyword in path for keyword in COURSE_HINT_KEYWORDS):
        score += 4.0
    if any(keyword in combined for keyword in COURSE_HINT_KEYWORDS):
        score += 2.0
    if query and query.lower() in combined:
        score += 2.0
    if any(domain.endswith(suffix) for suffix in (".edu", ".ac.uk", ".ac.jp", ".edu.au")):
        score += 1.5
    if any(keyword in combined for keyword in NEGATIVE_HINT_KEYWORDS):
        score -= 3.0

    return score


def _is_university_like_domain(domain: str) -> bool:
    return any(domain.endswith(suffix) for suffix in (".edu", ".ac.uk", ".ac.jp", ".edu.au"))


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if len(token) >= 3]


JP_UNIVERSITY_PATTERN = re.compile(
    r"([一-龠々ぁ-んァ-ンA-Za-z0-9・\-\s]{1,80}(?:大学|大学院|短期大学|高等専門学校|専門学校))"
)
EN_UNIVERSITY_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9&'\.\-\s]{1,100}(?:University|College|Institute|School|Polytechnic|Academy))",
    re.IGNORECASE,
)
NOISY_NAME_PREFIX_PATTERN = re.compile(
    r"^(?:私は|僕は|ぼくは|私が|当方は|i am|i'm|i study at|i studied at|my university is)\s*",
    re.IGNORECASE,
)


def _trim_name_prefix(name: str) -> str:
    text = (name or "").strip()
    # Sentence-like leading phrases degrade query quality; trim them first.
    while True:
        replaced = NOISY_NAME_PREFIX_PATTERN.sub("", text).strip()
        if replaced == text:
            return text
        text = replaced


def _extract_university_name(text: str) -> str:
    cleaned = _trim_name_prefix(" ".join((text or "").strip().split()))
    if not cleaned:
        return ""

    jp_match = JP_UNIVERSITY_PATTERN.search(cleaned)
    if jp_match:
        return _trim_name_prefix(jp_match.group(1)).strip(" ,.;:()[]{}\"'「」『』")

    en_match = EN_UNIVERSITY_PATTERN.search(cleaned)
    if en_match:
        return _trim_name_prefix(en_match.group(1)).strip(" ,.;:()[]{}\"'「」『』")

    return cleaned.strip(" ,.;:()[]{}\"'「」『』")


def _normalize_university_names(names: list[str], candidate_lines: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        v = _extract_university_name(value)
        if not v or len(v) < 2:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        normalized.append(v)

    for name in names:
        _push(name)

    if normalized:
        return normalized

    # Fallback: try to recover names from candidate lines when extracted names are missing/noisy.
    for line in candidate_lines[:20]:
        extracted = _extract_university_name(line)
        if extracted != line or JP_UNIVERSITY_PATTERN.search(extracted) or EN_UNIVERSITY_PATTERN.search(extracted):
            _push(extracted)

    return normalized


def _domain_match_bonus(domain: str, university_names: list[str]) -> float:
    domain_text = domain.lower()
    bonus = 0.0
    for name in university_names:
        for token in _tokens(name):
            if token in {"university", "college", "institute", "school"}:
                continue
            if token in domain_text:
                bonus += 0.8
    return bonus


def _extract_page_signals(url: str, title: str, snippet: str, html: str) -> tuple[bool, bool, float]:
    text = f"{url} {title} {snippet} {html[:50000]}".lower()
    path = (urlparse(url).path or "").lower()

    search_form_detected = "<form" in html.lower() and any(
        keyword in text for keyword in ("search", "finder", "filter", "query")
    )

    course_keyword_hits = sum(1 for keyword in COURSE_HINT_KEYWORDS if keyword in text)
    course_list_detected = course_keyword_hits >= 2 or "/program" in path or "/course" in path

    score_bonus = 0.0
    if search_form_detected:
        score_bonus += 1.0
    if course_list_detected:
        score_bonus += 3.0

    return search_form_detected, course_list_detected, score_bonus


def _apply_probe_to_hit(hit: SearchHit, html: str) -> SearchHit:
    search_form_detected, course_list_detected, score_bonus = _extract_page_signals(
        url=hit.url,
        title=hit.title,
        snippet=hit.snippet,
        html=html,
    )
    updated_score = hit.score + score_bonus
    return SearchHit(
        query=hit.query,
        url=hit.url,
        title=hit.title,
        snippet=hit.snippet,
        score=updated_score,
        is_course_like=hit.is_course_like or course_list_detected or updated_score >= 4.0,
        search_form_detected=search_form_detected,
        course_list_detected=course_list_detected,
    )


def _extract_internal_course_links(base_url: str, html: str) -> list[str]:
    if not html:
        return []

    base_domain = _domain(base_url)
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    links: list[str] = []
    for href in hrefs:
        href = href.strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        if _domain(absolute) != base_domain:
            continue
        path = (urlparse(absolute).path or "").lower()
        if any(keyword in path for keyword in COURSE_HINT_KEYWORDS):
            if absolute not in links:
                links.append(absolute)
        if len(links) >= MAX_INTERNAL_LINKS_PER_DOMAIN:
            break
    return links


def _extract_sitemap_directives(robots_text: str) -> list[str]:
    sitemap_urls: list[str] = []
    for line in robots_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("sitemap:"):
            url = stripped.split(":", 1)[1].strip()
            if url and _is_valid_absolute_http_url(url):
                sitemap_urls.append(url)
    return sitemap_urls


def _parse_sitemap_urls(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (urlset loc urls, nested sitemap urls)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    ns = ""
    if "}" in root.tag:
        ns = root.tag[: root.tag.rfind("}") + 1]
    tag_name = root.tag.split("}")[-1]

    urls: list[str] = []
    nested: list[str] = []

    if tag_name == "urlset":
        for url_elem in root.findall(f"{ns}url"):
            loc_elem = url_elem.find(f"{ns}loc")
            if loc_elem is not None and loc_elem.text:
                urls.append(loc_elem.text.strip())
    elif tag_name == "sitemapindex":
        for sitemap_elem in root.findall(f"{ns}sitemap"):
            loc_elem = sitemap_elem.find(f"{ns}loc")
            if loc_elem is not None and loc_elem.text:
                nested.append(loc_elem.text.strip())

    return urls, nested


def _discover_sitemap_course_urls(official_domains: set[str], errors: list[str]) -> list[str]:
    discovered: list[str] = []

    for domain in sorted(official_domains):
        if not domain:
            continue

        sitemap_candidates = [f"https://{domain}/sitemap.xml"]
        robots_url = f"https://{domain}/robots.txt"
        try:
            robots_resp = requests.get(robots_url, headers=DEFAULT_HEADERS, timeout=10, allow_redirects=True)
            if robots_resp.status_code == 200:
                for sm in _extract_sitemap_directives(robots_resp.text):
                    if sm not in sitemap_candidates:
                        sitemap_candidates.append(sm)
        except requests.RequestException as exc:
            errors.append(f"sitemap_robots_failed domain={domain}: {type(exc).__name__}: {exc}")

        queue: list[tuple[str, int]] = [(url, 0) for url in sitemap_candidates]
        visited_sitemaps: set[str] = set()

        while queue:
            sitemap_url, depth = queue.pop(0)
            if depth > MAX_SITEMAP_RECURSION:
                continue
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)

            try:
                response = requests.get(sitemap_url, headers=DEFAULT_HEADERS, timeout=12, allow_redirects=True)
                response.raise_for_status()
            except requests.RequestException as exc:
                errors.append(f"sitemap_fetch_failed url={sitemap_url}: {type(exc).__name__}: {exc}")
                continue

            urls, nested_sitemaps = _parse_sitemap_urls(response.text)
            for nested in nested_sitemaps:
                if nested not in visited_sitemaps:
                    queue.append((nested, depth + 1))

            for url in urls:
                if _domain(url) != domain:
                    continue
                path = (urlparse(url).path or "").lower()
                if not any(keyword in path for keyword in COURSE_HINT_KEYWORDS):
                    continue
                if url not in discovered:
                    discovered.append(url)
                if len([u for u in discovered if _domain(u) == domain]) >= MAX_SITEMAP_URLS_PER_DOMAIN:
                    break

    return discovered


def _probe_hit_sync(hit: SearchHit, errors: list[str]) -> SearchHit:
    try:
        response = requests.get(hit.url, headers=DEFAULT_HEADERS, timeout=10, allow_redirects=True)
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        html = response.text if "text/html" in content_type else ""
        return _apply_probe_to_hit(hit, html)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None and int(status) >= 500:
            errors.append(f"probe_sync_server_error url={hit.url}: HTTP {status}")
        return hit
    except requests.RequestException as exc:
        errors.append(f"probe_sync url={hit.url}: {type(exc).__name__}: {exc}")
        return hit


async def _probe_hit_async(
    hit: SearchHit,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    errors: list[str],
) -> SearchHit:
    async with semaphore:
        try:
            response = await client.get(hit.url, follow_redirects=True)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            html = response.text if "text/html" in content_type else ""
            return _apply_probe_to_hit(hit, html)
        except httpx.RequestError as exc:
            # 接続失敗時は requests で再試行
            errors.append(f"probe_httpx_failed url={hit.url}: {type(exc).__name__}: {exc}")
            return await asyncio.to_thread(_probe_hit_sync, hit, errors)
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code)
            if status >= 500:
                errors.append(f"probe_status_server_error url={hit.url}: HTTP {status}")
            return hit


async def _probe_hits_async(hits: list[SearchHit], errors: list[str]) -> list[SearchHit]:
    if not hits:
        return hits
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
        tasks = [
            _probe_hit_async(hit=hit, client=client, semaphore=semaphore, errors=errors)
            for hit in hits
        ]
        return await asyncio.gather(*tasks)


def _probe_urls_sync(urls: list[str], errors: list[str]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for url in urls:
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10, allow_redirects=True)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            html = response.text if "text/html" in content_type else ""
            base_score = _course_like_score(url=url, title="", snippet="", query="internal-link") + 2.0
            base_hit = SearchHit(
                query="internal-link",
                url=url,
                title="",
                snippet="",
                score=base_score,
                is_course_like=base_score >= 4.0,
            )
            hits.append(_apply_probe_to_hit(base_hit, html))
        except requests.HTTPError:
            # 4xx は探索ノイズとして扱い、error_count を増やさない。
            continue
        except requests.RequestException as exc:
            errors.append(f"internal_probe_sync url={url}: {type(exc).__name__}: {exc}")
    return hits


async def _probe_urls_async(urls: list[str], errors: list[str]) -> list[SearchHit]:
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def _one(url: str, client: httpx.AsyncClient) -> SearchHit | None:
        async with semaphore:
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                html = response.text if "text/html" in content_type else ""
                base_score = _course_like_score(url=url, title="", snippet="", query="internal-link") + 2.0
                base_hit = SearchHit(
                    query="internal-link",
                    url=url,
                    title="",
                    snippet="",
                    score=base_score,
                    is_course_like=base_score >= 4.0,
                )
                return _apply_probe_to_hit(base_hit, html)
            except httpx.RequestError as exc:
                errors.append(f"internal_probe_httpx_failed url={url}: {type(exc).__name__}: {exc}")
                sync_hits = await asyncio.to_thread(_probe_urls_sync, [url], errors)
                return sync_hits[0] if sync_hits else None
            except httpx.HTTPStatusError as exc:
                if int(exc.response.status_code) >= 500:
                    errors.append(f"internal_probe_status_server_error url={url}: HTTP {exc.response.status_code}")
                return None

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
        results = await asyncio.gather(*[_one(url, client) for url in urls])
    return [hit for hit in results if hit is not None]


def _enrich_hits_with_probe(hits: list[SearchHit], errors: list[str]) -> list[SearchHit]:
    top_hits = [
        hit
        for hit in hits
        if not _is_blocked_seed_domain(_domain(hit.url)) and _is_valid_absolute_http_url(hit.url)
    ][:MAX_PROBE_URLS]
    if not top_hits:
        return hits

    try:
        asyncio.get_running_loop()
        # 既存イベントループ内では同期フォールバックで安全に処理する。
        enriched_top = [_probe_hit_sync(hit, errors) for hit in top_hits]
    except RuntimeError:
        enriched_top = asyncio.run(_probe_hits_async(top_hits, errors))

    by_url: dict[str, SearchHit] = {hit.url: hit for hit in hits}
    for hit in enriched_top:
        by_url[hit.url] = hit
    return sorted(by_url.values(), key=lambda h: h.score, reverse=True)


def _discover_internal_links_from_official_hits(
    hits: list[SearchHit],
    official_domains: set[str],
    errors: list[str],
) -> list[str]:
    targets = [hit for hit in hits if _domain(hit.url) in official_domains][:MAX_PROBE_URLS]
    if not targets:
        return []

    discovered: list[str] = []
    for hit in targets:
        try:
            response = requests.get(hit.url, headers=DEFAULT_HEADERS, timeout=10, allow_redirects=True)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            html = response.text if "text/html" in content_type else ""
            links = _extract_internal_course_links(hit.url, html)
            for link in links:
                if link not in discovered:
                    discovered.append(link)
        except requests.RequestException as exc:
            errors.append(f"internal_extract url={hit.url}: {type(exc).__name__}: {exc}")

    return discovered


def _select_official_domains(hits: list[SearchHit], university_names: list[str]) -> set[str]:
    domain_scores: dict[str, float] = {}
    fallback_scores: dict[str, float] = {}
    for hit in hits:
        d = _domain(hit.url)
        if not d:
            continue
        if _is_blocked_seed_domain(d):
            continue

        raw_score = hit.score
        prev_raw = fallback_scores.get(d, float("-inf"))
        if raw_score > prev_raw:
            fallback_scores[d] = raw_score

        name_bonus = _domain_match_bonus(d, university_names)
        university_like = _is_university_like_domain(d)
        # 公式候補は「大学系TLD」または「大学名トークン一致」を満たすものに限定する。
        if not university_like and name_bonus < 0.8:
            continue

        score = hit.score
        if university_like:
            score += 2.0
        score += name_bonus
        prev = domain_scores.get(d, float("-inf"))
        if score > prev:
            domain_scores[d] = score

    selected = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    selected_domains = {d for d, _ in selected}
    if selected_domains:
        return selected_domains

    # Fallback: strict filterで候補が0件なら、非ブロックかつ高スコアの上位ドメインを使う。
    fallback_selected = sorted(fallback_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    return {d for d, _ in fallback_selected}


def _existing_root_urls(candidate_roots: list[str]) -> set[str]:
    db_params = _get_db_params()
    if not db_params or not candidate_roots:
        return set()

    existing: set[str] = set()
    try:
        with psycopg2.connect(**db_params) as conn:
            with conn.cursor() as cursor:
                set_search_path(cursor, get_observer_schema(), get_public_schema())
                for root in candidate_roots:
                    domain = _domain(root)
                    cursor.execute(
                        f"SELECT 1 FROM {SEED_URLS_TABLE} WHERE domain = %s AND root_url = %s LIMIT 1",
                        (domain, root),
                    )
                    if cursor.fetchone() is not None:
                        existing.add(root)
    except psycopg2.Error:
        # DB 不達時は探索を止めない
        return set()
    return existing


def _build_domain_discovery_queries(request: SearchRequest) -> list[str]:
    queries, _ = _build_domain_discovery_queries_with_stats(request)
    return queries


def _build_domain_discovery_queries_with_stats(request: SearchRequest) -> tuple[list[str], dict[str, int]]:
    max_queries_raw = os.getenv("SEARCH_DISCOVERY_QUERY_LIMIT", str(DEFAULT_MAX_DISCOVERY_QUERIES)).strip()
    try:
        max_queries = max(1, int(max_queries_raw))
    except ValueError:
        max_queries = DEFAULT_MAX_DISCOVERY_QUERIES

    per_uni_raw = os.getenv(
        "SEARCH_DISCOVERY_PER_UNIVERSITY_LIMIT",
        str(DEFAULT_DISCOVERY_PER_UNIVERSITY_LIMIT),
    ).strip()
    try:
        per_uni_limit = max(1, int(per_uni_raw))
    except ValueError:
        per_uni_limit = DEFAULT_DISCOVERY_PER_UNIVERSITY_LIMIT

    grouped_queries: list[list[str]] = []
    dropped_empty_names = 0

    for name in request.university_names:
        n = name.strip()
        if not n:
            dropped_empty_names += 1
            continue
        # SEARCH_DISCOVERY_PER_UNIVERSITY_LIMIT で先頭から使うため、大学名の言語に合う語を先にする
        if re.search(r"[぀-ヿ一-鿿]", n):
            per_name = [f"{n} 公式", f"{n} official", f"{n} official site"]
        else:
            per_name = [f"{n} official", f"{n} official site", f"{n} 公式"]
        deduped_per_name: list[str] = []
        seen_per_name: set[str] = set()
        for query in per_name:
            if query in seen_per_name:
                continue
            seen_per_name.add(query)
            deduped_per_name.append(query)
        grouped_queries.append(deduped_per_name[:per_uni_limit])

    all_generated_count = sum(len(group) for group in grouped_queries)

    # 同じ大学だけが上限を使い切らないよう、大学ごとに1件ずつラウンドロビンで配分する
    selected: list[str] = []
    seen_global: set[str] = set()
    round_index = 0
    while len(selected) < max_queries:
        added_in_round = False
        for group in grouped_queries:
            if round_index >= len(group):
                continue
            q = group[round_index]
            if q in seen_global:
                continue
            seen_global.add(q)
            selected.append(q)
            added_in_round = True
            if len(selected) >= max_queries:
                break
        if not added_in_round:
            break
        round_index += 1

    # 候補が空なら source_domain を使った保険クエリを投げる
    used_source_domain_fallback = 0
    if not selected and request.source_domain:
        selected.append(f"{request.source_domain} official")
        used_source_domain_fallback = 1

    stats = {
        "university_count": len(request.university_names),
        "dropped_empty_names": dropped_empty_names,
        "all_generated_count": all_generated_count,
        "selected_count": len(selected),
        "dropped_by_global_limit": max(0, all_generated_count - len(selected)),
        "global_limit": max_queries,
        "per_university_limit": per_uni_limit,
        "used_source_domain_fallback": used_source_domain_fallback,
    }
    return selected, stats


def _build_course_fallback_queries(official_domains: set[str], request: SearchRequest) -> list[str]:
    queries: list[str] = []
    for domain in sorted(official_domains):
        queries.append(f"site:{domain} courses")
        queries.append(f"site:{domain} programmes")
    if not queries:
        for name in request.university_names:
            n = name.strip()
            if n:
                queries.append(f"{n} courses")
                break
    deduped: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if q not in seen:
            deduped.append(q)
            seen.add(q)
    return deduped[:MAX_FALLBACK_QUERIES]


def build_search_request(item: ObserveStackItem) -> SearchRequest:
    normalized_names = _normalize_university_names(
        list(item.university_names),
        list(item.page_analysis.candidate_lines),
    )
    return SearchRequest(
        source_url=item.source_url,
        source_domain=_domain(item.source_url),
        university_names=normalized_names,
        content_type=item.page_analysis.content_type.value,
        candidate_lines=list(item.page_analysis.candidate_lines),
    )


def _build_result(
    request: SearchRequest,
    hits: list[SearchHit],
    errors: list[str],
    official_domains: set[str] | None = None,
) -> SearchResult:
    official = set(official_domains or set())
    allowed_hits = [
        hit
        for hit in hits
        if not _is_blocked_seed_domain(_domain(hit.url))
        and (not official or _domain(hit.url) in official)
    ]

    root_candidates: list[str] = []
    for hit in allowed_hits:
        root = _root_url(hit.url)
        if root and root not in root_candidates:
            root_candidates.append(root)

    duplicate_roots = _existing_root_urls(root_candidates)
    filtered_roots = [root for root in root_candidates if root not in duplicate_roots]

    detailed_seed_urls = [
        hit.url
        for hit in allowed_hits
        if hit.is_course_like and _root_url(hit.url) not in duplicate_roots
    ]

    course_list_found = any(hit.is_course_like for hit in allowed_hits)
    if course_list_found:
        recommended_depth = 1
    elif allowed_hits:
        recommended_depth = 2
    else:
        recommended_depth = 3

    return SearchResult(
        source_url=request.source_url,
        source_domain=request.source_domain,
        university_names=request.university_names,
        hits=hits,
        root_seed_urls=filtered_roots,
        detailed_seed_urls=detailed_seed_urls,
        course_list_found=course_list_found,
        recommended_depth=recommended_depth,
        duplicate_root_urls=sorted(duplicate_roots),
        errors=errors,
    )


def _load_cached_seed_result(request: SearchRequest) -> SearchResult | None:
    observe_log_dsn = _get_observe_log_dsn()
    if not observe_log_dsn or psycopg2 is None:
        return None

    try:
        with psycopg2.connect(observe_log_dsn) as conn:
            with conn.cursor() as cursor:
                set_search_path(cursor, get_observer_schema(), get_public_schema())
                cursor.execute(
                    f"""
                    SELECT
                        root_seed_urls,
                        detailed_seed_urls,
                        course_list_found,
                        recommended_depth
                    FROM {SEED_OBSERVE_RESULTS_TABLE}
                    WHERE source_url = %s
                      AND (
                        jsonb_array_length(coalesce(root_seed_urls, '[]'::jsonb)) > 0
                        OR jsonb_array_length(coalesce(detailed_seed_urls, '[]'::jsonb)) > 0
                      )
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (request.source_url,),
                )
                row = cursor.fetchone()
    except psycopg2.Error:
        return None

    if row is None:
        return None

    root_seed_urls, detailed_seed_urls, course_list_found, recommended_depth = row
    cached_roots: list[str] = []
    for url in _json_to_string_list(root_seed_urls) + _json_to_string_list(detailed_seed_urls):
        root = _root_url(url)
        if root and root not in cached_roots:
            cached_roots.append(root)

    if not cached_roots:
        return None

    existing_cached_roots = _existing_root_urls(cached_roots)
    if len(existing_cached_roots) != len(cached_roots):
        return None

    return SearchResult(
        source_url=request.source_url,
        source_domain=request.source_domain,
        university_names=request.university_names,
        hits=[],
        root_seed_urls=[],
        detailed_seed_urls=[],
        course_list_found=bool(course_list_found),
        recommended_depth=int(recommended_depth) if recommended_depth is not None else 3,
        duplicate_root_urls=sorted(existing_cached_roots),
        errors=[
            f"search_skipped_cached_seed_urls: source_url={request.source_url} cached_roots={len(cached_roots)}"
        ],
    )


def search_seeds(item: ObserveStackItem, api: BraveSearchAPI | None = None) -> SearchResult:
    return _search_seeds_sync(item=item, api=api)


def _to_transform_input(
    result: SearchResult,
    *,
    api_type: str,
    first_search_count: int,
    internal_link_extracted_count: int,
    fallback_executed: bool,
    api_usage_count: int,
    search_queries: list[str],
    run_id: int | None,
    source_stage: str,
) -> SeedTransformInput:
    return SeedTransformInput(
        source_url=result.source_url,
        source_domain=result.source_domain,
        university_names=list(result.university_names),
        hits=list(result.hits),
        root_seed_urls=list(result.root_seed_urls),
        detailed_seed_urls=list(result.detailed_seed_urls),
        course_list_found=result.course_list_found,
        recommended_depth=result.recommended_depth,
        duplicate_root_urls=list(result.duplicate_root_urls),
        errors=list(result.errors),
        api_type=api_type,
        first_search_count=first_search_count,
        internal_link_extracted_count=internal_link_extracted_count,
        fallback_executed=fallback_executed,
        api_usage_count=api_usage_count,
        search_queries=list(search_queries),
        run_id=run_id,
        source_stage=source_stage,
    )


def _search_seeds_sync(item: ObserveStackItem, api: BraveSearchAPI | None = None) -> SeedTransformInput:
    request = build_search_request(item)
    cached_result = _load_cached_seed_result(request)
    if cached_result is not None:
        return _to_transform_input(
            cached_result,
            api_type="cache-seed-urls",
            first_search_count=0,
            internal_link_extracted_count=0,
            fallback_executed=False,
            api_usage_count=0,
            search_queries=[],
            run_id=item.observe_run_id,
            source_stage="seed_searcher",
        )

    client_api = api or BraveSearchAPI()
    #fallback_api = PlaywrightFallbackSearch()
    queries, query_stats = _build_domain_discovery_queries_with_stats(request)
    api_usage_count = 0
    api_queries_used: list[str] = []
    used_provider_types: set[str] = set()
    first_search_count = 0
    internal_link_extracted_count = 0
    fallback_executed = False

    hits_by_url: dict[str, SearchHit] = {}
    errors: list[str] = []

    errors.append(
        "query_generation: "
        f"universities={query_stats['university_count']} "
        f"generated={query_stats['all_generated_count']} "
        f"selected={query_stats['selected_count']} "
        f"dropped_global={query_stats['dropped_by_global_limit']} "
        f"dropped_empty={query_stats['dropped_empty_names']} "
        f"global_limit={query_stats['global_limit']} "
        f"per_university_limit={query_stats['per_university_limit']} "
        f"source_domain_fallback={query_stats['used_source_domain_fallback']}"
    )

    if not client_api.enabled:
        errors.append("BRAVE_API_KEY is not set. try playwright fallback.")
    #if not fallback_api.enabled:
    #    errors.append("playwright fallback is disabled by PLAYWRIGHT_GOOGLE_FALLBACK.")

    for query in queries:
        api_queries_used.append(query)
        rows, provider_type, provider_fallback_used = _search_with_provider_fallback(
            query=query,
            num_results=8,
            primary=client_api,
            #secondary=fallback_api,
            errors=errors,
        )
        api_usage_count += 1
        if provider_type:
            used_provider_types.add(provider_type)
        if provider_fallback_used:
            fallback_executed = True

        first_search_count += len(rows)

        for row in rows:
            url = row["url"]
            score = _course_like_score(url=url, title=row["title"], snippet=row["snippet"], query=query)
            hit = SearchHit(
                query=query,
                url=url,
                title=row["title"],
                snippet=row["snippet"],
                score=score,
                is_course_like=score >= 4.0,
            )
            current = hits_by_url.get(url)
            if current is None or hit.score > current.score:
                hits_by_url[url] = hit

    hits = sorted(hits_by_url.values(), key=lambda h: h.score, reverse=True)
    hits = _enrich_hits_with_probe(hits, errors)

    # 1) 公式ドメイン推定
    official_domains = _select_official_domains(hits, request.university_names)

    # 1.5) 公式ドメインの sitemap からコース一覧候補を先取り
    sitemap_urls = _discover_sitemap_course_urls(official_domains, errors)
    for sitemap_url in sitemap_urls:
        score = _course_like_score(
            url=sitemap_url,
            title="sitemap candidate",
            snippet="sitemap discovered",
            query="sitemap",
        ) + 3.0
        hit = SearchHit(
            query="sitemap",
            url=sitemap_url,
            title="sitemap candidate",
            snippet="sitemap discovered",
            score=score,
            is_course_like=True,
            course_list_detected=True,
        )
        current = hits_by_url.get(sitemap_url)
        if current is None or hit.score > current.score:
            hits_by_url[sitemap_url] = hit

    # 2) 公式ドメイン内の内部リンク探索（courses/programmes っぽいリンク抽出）
    internal_links = _discover_internal_links_from_official_hits(hits, official_domains, errors)
    internal_link_extracted_count = len(internal_links)
    if internal_links:
        try:
            asyncio.get_running_loop()
            internal_hits = _probe_urls_sync(internal_links, errors)
        except RuntimeError:
            internal_hits = asyncio.run(_probe_urls_async(internal_links, errors))
        for ihit in internal_hits:
            prev = hits_by_url.get(ihit.url)
            if prev is None or ihit.score > prev.score:
                hits_by_url[ihit.url] = ihit

    # 2.5) コース一覧が取れなければ条件付きで再検索
    merged_hits = sorted(hits_by_url.values(), key=lambda h: h.score, reverse=True)
    if not any(hit.course_list_detected or hit.is_course_like for hit in merged_hits):
        fallback_queries = _build_course_fallback_queries(official_domains, request)
        fallback_executed = fallback_executed or bool(fallback_queries)
        for query in fallback_queries:
            api_queries_used.append(query)
            rows, provider_type, provider_fallback_used = _search_with_provider_fallback(
                query=query,
                num_results=6,
                primary=client_api,
                #secondary=fallback_api,
                errors=errors,
            )
            api_usage_count += 1
            if provider_type:
                used_provider_types.add(provider_type)
            if provider_fallback_used:
                fallback_executed = True

            for row in rows:
                url = row["url"]
                score = _course_like_score(url=url, title=row["title"], snippet=row["snippet"], query=query)
                hit = SearchHit(
                    query=query,
                    url=url,
                    title=row["title"],
                    snippet=row["snippet"],
                    score=score,
                    is_course_like=score >= 4.0,
                )
                current = hits_by_url.get(url)
                if current is None or hit.score > current.score:
                    hits_by_url[url] = hit

        merged_hits = sorted(hits_by_url.values(), key=lambda h: h.score, reverse=True)
        merged_hits = _enrich_hits_with_probe(merged_hits, errors)

    hits = merged_hits
    # 投入時は「非ブロックドメイン」だけを要件として扱い、公式ドメイン一致は強制しない。
    result = _build_result(request, hits, errors, official_domains=None)
    resolved_api_type = _resolve_api_type(
        primary_api_type=client_api.api_type,
        #fallback_api_type=fallback_api.api_type,
        used_provider_types=used_provider_types,
    )

    if not hits:
        result.errors.append("search_failed: no hits produced by brave or playwright fallback")

    search_log_store = SearchLogStore.from_env()
    if search_log_store is not None:
        try:
            search_log_store.init_db()
            search_log_store.insert_run_log(
                SearchRunLogRecord(
                    source_url=request.source_url,
                    source_domain=request.source_domain,
                    api_type=resolved_api_type,
                    first_search_count=first_search_count,
                    internal_link_extracted_count=internal_link_extracted_count,
                    fallback_executed=fallback_executed,
                    api_usage_count=api_usage_count,
                    run_id=item.observe_run_id,
                    source_stage="seed_searcher",
                )
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"search_log_write_failed: {type(exc).__name__}: {exc}")
        finally:
            search_log_store.close()

    return _to_transform_input(
        result,
        api_type=resolved_api_type,
        first_search_count=first_search_count,
        internal_link_extracted_count=internal_link_extracted_count,
        fallback_executed=fallback_executed,
        api_usage_count=api_usage_count,
        search_queries=api_queries_used,
        run_id=item.observe_run_id,
        source_stage="seed_searcher",
    )


def to_seed_discovery(result: SearchResult) -> SeedDiscovery:
    seed_urls = list(result.detailed_seed_urls)
    for root in result.root_seed_urls:
        if root not in seed_urls:
            seed_urls.append(root)

    discovery = SeedDiscovery(
        domain_url=result.source_url,
        seed_urls=seed_urls,
        depth=result.recommended_depth,
    )
    for hit in result.hits:
        discovery.add_seed_candidate(hit.url, hit.score)
    return discovery


def handle_observe_item(item: ObserveStackItem) -> SeedTransformInput:
    result = search_seeds(item)
    print(
        "[OBSERVE_SEARCHER] "
        f"source={result.source_url} "
        f"universities={len(result.university_names)} "
        f"hits={len(result.hits)} "
        f"roots={len(result.root_seed_urls)} "
        f"course_like={result.course_list_found} "
        f"depth={result.recommended_depth} "
        f"dupes={len(result.duplicate_root_urls)}"
    )
    if result.errors:
        print(f"[OBSERVE_SEARCHER] errors={'; '.join(result.errors)}")
    return result
