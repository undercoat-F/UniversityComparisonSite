#fetchのみを行い、一つのURLを探索することのみを責務とする
"""
予定

SiteState
{
    "domain": "",
    "start_urls": [],
    "crawl_delay": 0,
    "last_access": 0,
    "semaphore": 1,
    "user_agent": "",
    "robots_fetched_at": "",
    "status": "active",
    "stats": {
        "success": 0,
        "errors": 0,
        "total_time": 0
    }
}

URLTask
{
    "url": "",
    "depth": 0,
    "status": "pending",
    "retry_count": 0,
    "discovered_from": "",
    "queued_at": ""
}


"""
import re
import asyncio
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import requests
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

from dataclass.dataclass import CrawlAttempt, FetchResult, SiteState, URLTask

DEFAULT_TIMEOUT = 30


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


SITEMAP_SEED_MAX_SECONDS = _env_int("ETL_SITEMAP_SEED_MAX_SECONDS", 60, 1)
SITEMAP_SEED_MAX_SITEMAPS = _env_int("ETL_SITEMAP_SEED_MAX_SITEMAPS", 40, 1)
SITEMAP_SEED_MAX_CANDIDATES_PER_DOMAIN = _env_int(
    "ETL_SITEMAP_SEED_MAX_CANDIDATES_PER_DOMAIN", 500, 1
)
TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN = _env_int("ETL_TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN", 20, 0)
REQUESTS_TIMEOUT_SEC = _env_int("ETL_REQUESTS_TIMEOUT_SEC", DEFAULT_TIMEOUT, 1)
REQUESTS_ROBOTS_TIMEOUT_SEC = _env_int("ETL_REQUESTS_ROBOTS_TIMEOUT_SEC", 10, 1)
REQUESTS_SITEMAP_TIMEOUT_SEC = _env_int("ETL_REQUESTS_SITEMAP_TIMEOUT_SEC", 15, 1)
ROBOTS_READ_TIMEOUT_SEC = _env_int("ETL_ROBOTS_READ_TIMEOUT_SEC", 20, 1)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SITEMAP_PRIORITY_KEYWORDS = [
    "program",
    "programme",
    "course",
    "degree",
    "study",
    "tuition",
    "fee",
    "undergraduate",
    "postgraduate",
    "master",
    "bachelor",
    "phd",
]

keywords = ["学位","学士","修士","博士",
            "degree", "bachelor","undergraduate","BSc","BA","BEng",
              "master","graduate","MSc","MA","MEng","MBA", 
              "phd","doctorate","doctoral","DSc","Ph.D.","Dr.",
              "program", "course", "study"]

# True: URL_Filterを適用 / False: URL_Filterを無効化
USE_URL_FILTER = True

class FetchWithFallbackError(Exception):
    def __init__(self, message: str, connection_log: list[dict[str, Any]], status_code: Optional[int] = None):
        super().__init__(message)
        self.connection_log = connection_log
        self.status_code = status_code


"""
Optionalの主な使いどころは、メソッドの戻り値として使用することです。 
従来ならnullを返すメソッドの戻り値をOptional型にすることによって、
値が存在しない場合があることを明示することができます。
例えば、あるユーザーをIDで検索するメソッドがあるとします。
従来は、ユーザーが見つからない場合にnullを返すことが一般的でしたが、
Optional<User>を返すようにすることで、呼び出し側はユーザーが存在しない可能性を考慮する必要があります。
Optionalを使用することで、コードの安全性が向上し、NullPointerExceptionのリスクを減らすことができます。
"""

def same_domain(base_domain: str, candidate_url: str) -> bool:
    return urlparse(candidate_url).netloc == base_domain


def extract_same_domain_links(html_text: str, base_url: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    discovered = []
    for anchor in soup.find_all("a", href=True):
        candidate = urljoin(base_url, anchor["href"])
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        normalized = parsed._replace(fragment="").geturl()
        if same_domain(domain, normalized):
            discovered.append(normalized)
    return discovered

def bot_check_page(html_text):
    text = (html_text or "").lower()
    bot_markers = [
        "captcha",
        "cloudflare",
        "verify you are human",
        "are you human",
        "access denied",
        "bot detection",
    ]
    return any(marker in text for marker in bot_markers)

def check_keywords_in_text(text, keywords):
    for keyword in keywords:
        if keyword in text:
            return True
    return False

def normalize_scope_path(url):
    path = urlparse(url).path.rstrip("/")
    return path or "/"

def is_url_in_scope(full_url, target_url):
    target_parsed = urlparse(target_url)
    full_parsed = urlparse(full_url)
    if full_parsed.netloc != target_parsed.netloc:
        return False

    scope_path = normalize_scope_path(target_url)
    candidate_path = full_parsed.path.rstrip("/") or "/"

    if scope_path == "/":
        return True

    return candidate_path == scope_path or candidate_path.startswith(scope_path + "/")

def should_exclude_course_page(url, title):
    text = f"{title} {urlparse(url).path}".lower()
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")
    query = parsed.query.lower()
    exclude_markers = [
        "scholarship",
        "bursar",
        "funding",
        "fellowship",
        "graduation",
        "alumni",
        "student guide",
        "support-wellbeing",
        "support and wellbeing",
        "recognition-prior-learning",
        "recognition of prior learning",
        "how-apply",
        "how to apply",
        "where-study",
        "where to study",
        "why-study-us",
        "why study with us",
        "on the day",
        "after-graduation",
        "after graduation",
        "am-qualified",
        "am i qualified",
        "costs-course",
        "costs of your course",
        "current-students",
        "student-services",
        "taster-courses",
        "benefits",
    ]

    if "page=" in query:
        return True
    # 一覧ページを URL 規則で判定
    list_like_paths = {
        "/programmes",
        "/programmes/undergraduate",
        "/programmes/postgraduate-taught",
        "/programmes/postgraduate-research",
        "/study/courses",
        "/study/courses/undergraduate",
        "/study/courses/postgraduate",
        "/study/courses/research-degree",
    }
    if path in list_like_paths:
        return True

    # 検索/絞り込み付き一覧 URL を除外
    if "query=" in query and ("/programmes" in path or "/study/courses" in path):
        return True

    return any(marker in text for marker in exclude_markers)

def extract_urls(soup, base_url, target_url):
    urls = []
    for link in soup.find_all("a", href=True):
        href = link.get("href")
        full_url = urljoin(base_url, href)

        if is_url_in_scope(full_url, target_url):
            urls.append(full_url)

    return list(set(urls))

def is_non_html_url(url):
    """PDFや画像など、HTMLでないURLを判定（USE_URL_FILTERに関係なく常時適用）"""
    non_html_extensions = [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".rar", ".tar", ".gz",
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".mp4", ".mp3", ".avi", ".mov",
    ]
    base = url.lower().split("?")[0].split("#")[0]
    return any(base.endswith(ext) for ext in non_html_extensions)


def URL_Filter(url):

    bad_keywords = [
        "news", "event", "blog", "login",
        "contact", "about", "staff",
        "library"
    ]

    bad_extensions = [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".rar", ".tar", ".gz",
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".mp4", ".mp3", ".avi", ".mov",
    ]

    url_lower = url.lower().split("?")[0].split("#")[0]  # クエリ・フラグメントを除いて判定

    if any(url_lower.endswith(ext) for ext in bad_extensions):
        return True

    if any(k in url_lower for k in bad_keywords):
        return True

    return False

def can_fetch_url(site: SiteState, url: str) -> bool:
    if site.robots_parser is None:
        return True
    return site.robots_parser.can_fetch(site.user_agent, url)


def is_url_in_site_scope(site: SiteState, url: str) -> bool:
    if not site.start_urls:
        return same_domain(site.domain, url)
    return any(is_url_in_scope(url, seed_url) for seed_url in site.start_urls)


def should_queue_url(site: SiteState, url: str):
    """URLを探索キューに入れてよいかを一箇所で判定する。"""
    if url in site.visited or url in site.queued:
        return False

    if not is_url_in_site_scope(site, url):
        return False

    if is_non_html_url(url):
        return False

    if USE_URL_FILTER and URL_Filter(url):
        return False

    if not can_fetch_url(site, url):
        return False

    return True


def _should_log_tag_class_counts(site: SiteState, url: str) -> bool:
    limit = TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN
    if limit <= 0:
        return False
    if url in site.tag_class_logged_urls:
        return True
    if len(site.tag_class_logged_urls) >= limit:
        return False
    site.tag_class_logged_urls.add(url)
    return True

#抽出ロジック-----------------------------

def guess_country_from_url(url):
    #
    host = urlparse(url).netloc.lower()
    #
    if host.endswith(".ac.uk") or host.endswith(".gov.uk") or host.endswith(".uk"):
        return "UK"
    if host.endswith(".edu") or host.endswith(".gov"):
        return "US"
    if host.endswith(".ac.jp") or host.endswith(".go.jp") or host.endswith(".jp"):
        return "JP"
    return "unknown"

def canonicalize_url(url):
    # フラグメント/クエリ/末尾スラッシュ/年度セグメントを吸収して重複判定しやすくする
    x = url.strip().lower()
    x = re.sub(r"#.*$", "", x)
    x = re.sub(r"\?.*$", "", x)
    x = re.sub(r"/20\d{2}/", "/", x)
    x = x.rstrip("/")
    return x

def _keyword_hits(text, keyword):
    """英単語は単語境界で厳密一致、日本語などは部分一致で判定する。"""
    text_lower = text.lower()
    kw = keyword.lower()
    if re.search(r"[a-z]", kw):
        pattern = r"\b" + re.escape(kw) + r"\b"
        return len(re.findall(pattern, text_lower))
    return text_lower.count(kw)


COURSE_TYPE_KEYWORDS = {
    "computer_science": [
        "computer science", "informatics", "computing", "software", "programming", "ai", "artificial intelligence",
        "machine learning", "data science", "cybersecurity", "information systems", "情報", "データ", "計算機",
        "ソフトウェア", "人工知能", "機械学習", "情報科学",
    ],
    "engineering": [
        "engineering", "mechanical", "electrical", "electronic", "civil", "chemical engineering", "aerospace", "robotics",
        "materials", "industrial engineering", "工学", "機械", "電気", "電子", "土木", "化学工学", "材料", "ロボティクス",
    ],
    "business": [
        "business", "management", "finance", "mba", "accounting", "marketing", "economics and management",
        "entrepreneurship", "international business", "経営", "会計", "ビジネス", "ファイナンス", "マーケティング",
    ],
    "health": [
        "medicine", "medical", "nursing", "public health", "pharmacy", "biomedical", "clinical", "dentistry",
        "看護", "医学", "医療", "公衆衛生", "薬学", "歯学",
    ],
    "education": [
        "education", "teaching", "pedagogy", "curriculum", "teacher training", "educational", "教育", "教職", "教育学",
    ],
    "law": [
        "law", "legal", "jurisprudence", "llb", "llm", "法学", "法律", "司法",
    ],
    "social_science": [
        "psychology", "sociology", "politics", "political science", "economics", "international relations",
        "anthropology", "public policy", "社会", "心理", "経済", "政治", "国際関係", "社会学",
    ],
    "humanities": [
        "history", "philosophy", "literature", "language", "linguistics", "classics", "religion",
        "人文", "文学", "哲学", "歴史", "言語", "言語学",
    ],
    "science": [
        "biology", "chemistry", "physics", "mathematics", "science", "environmental science", "geology",
        "statistics", "生物", "化学", "物理", "数学", "理学", "統計", "地学", "環境科学",
    ],
    "arts_design": [
        "art", "design", "music", "fine art", "architecture", "media", "film", "theatre",
        "芸術", "デザイン", "音楽", "建築", "メディア", "映像", "演劇",
    ],
}

TAG_SCORE_WEIGHTS = {
    "title": 5,
    "h1": 5,
    "h2": 4,
    "h3": 4,
    "dt": 4,
    "th": 1,
    "td": 1,
    "p": 1,
    "li": 1,
    "a": 1,
}

#タグごとのキーワードヒット数を集計する。これを detect_course_type のスコア計算に利用する。
def collect_tag_keyword_hits(soup) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for tag_name, weight in TAG_SCORE_WEIGHTS.items():
        for tag in soup.find_all(tag_name):
            tag_text = tag.get_text(" ", strip=True)
            if not tag_text:
                continue
            text_lower = tag_text.lower()
            for course_type, words in COURSE_TYPE_KEYWORDS.items():
                for word in words:
                    hit_count = _keyword_hits(text_lower, word)
                    if hit_count <= 0:
                        continue
                    hits.append(
                        {
                            "tag_name": tag_name,
                            "course_type": course_type,
                            "keyword": word,
                            "hit_count": hit_count,
                            "weight": weight,
                            "weighted_score": hit_count * weight,
                        }
                    )
    return hits


def collect_tag_class_counts(soup) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for tag in soup.find_all(True):
        class_values = tag.get("class")
        if not class_values:
            continue
        tag_name = str(tag.name or "")
        if isinstance(class_values, (list, tuple, set)):
            names = [str(x).strip() for x in class_values if str(x).strip()]
        else:
            names = [part.strip() for part in str(class_values).split() if part.strip()]
        for class_name in names:
            counts[(tag_name, class_name)] += 1

    return [
        {
            "tag_name": tag_name,
            "class_name": class_name,
            "occurrence_count": occurrence_count,
        }
        for (tag_name, class_name), occurrence_count in counts.items()
    ]


def detect_course_type(primary_text, secondary_text="", heading_text="", tag_hits=None):
    """重み付きスコア方式: 見出しタグ(heading)を最優先(5倍)、近傍文脈(primary)を優先(3倍)、ページ全体(secondary)を補助利用(1倍)。"""
    primary_lower = primary_text.lower()
    secondary_lower = secondary_text.lower()
    heading_lower = heading_text.lower()

    # 各カテゴリのスコアを計算
    # tag_hits がある場合は、タグごとの weighted_score を最優先で加点する
    scores = {}
    primary_scores = {}
    secondary_scores = {}
    heading_scores = {}
    tag_weighted_scores = {course_type: 0 for course_type in COURSE_TYPE_KEYWORDS}
    for item in tag_hits or []:
        course_type = str(item.get("course_type", ""))
        if course_type in tag_weighted_scores:
            tag_weighted_scores[course_type] += int(item.get("weighted_score", 0) or 0)

    for course_type, words in COURSE_TYPE_KEYWORDS.items():
        heading_score = sum(_keyword_hits(heading_lower, word) for word in words) if heading_lower else 0
        primary_score = sum(_keyword_hits(primary_lower, word) for word in words)
        secondary_score = sum(_keyword_hits(secondary_lower, word) for word in words) if secondary_lower else 0
        tag_weighted_score = tag_weighted_scores.get(course_type, 0)
        heading_scores[course_type] = heading_score
        primary_scores[course_type] = primary_score
        secondary_scores[course_type] = secondary_score
        scores[course_type] = tag_weighted_score + (5 * heading_score) + (3 * primary_score) + secondary_score
    
    # 最高スコアを取得（スコア0なら"general"を返す）
    max_score = max(scores.values())
    if max_score == 0:
        return "general"
    
    # スコアが最高のカテゴリを抽出
    top_types = [k for k, v in scores.items() if v == max_score]
    if len(top_types) == 1:
        return top_types[0]

    # 同点時1: タグ weighted score が多いカテゴリを優先
    best_tag_weighted = max(tag_weighted_scores[t] for t in top_types)
    top_types = [t for t in top_types if tag_weighted_scores[t] == best_tag_weighted]
    if len(top_types) == 1:
        return top_types[0]

    # 同点時2: 見出し(heading)一致数が多いカテゴリを優先
    best_heading = max(heading_scores[t] for t in top_types)
    top_types = [t for t in top_types if heading_scores[t] == best_heading]
    if len(top_types) == 1:
        return top_types[0]

    # 同点時3: 近傍文脈(primary)一致数が多いカテゴリを優先
    best_primary = max(primary_scores[t] for t in top_types)
    top_types = [t for t in top_types if primary_scores[t] == best_primary]
    if len(top_types) == 1:
        return top_types[0]

    # 同点時4: ページ全体(secondary)一致数が多いカテゴリを優先
    best_secondary = max(secondary_scores[t] for t in top_types)
    top_types = [t for t in top_types if secondary_scores[t] == best_secondary]
    if len(top_types) == 1:
        return top_types[0]

    # 同点時5: 最終的な固定優先順（高頻度カテゴリへの偏りを抑える順）
    #この順がそのまま優先順になっている
    tie_break_priority = [
        "computer_science",
        "engineering",
        "health",
        "business",
        "law",
        "education",
        "social_science",
        "science",
        "humanities",
        "arts_design",
    ]
    for course_type in tie_break_priority:
        if course_type in top_types:
            return course_type

    # フォールバック
    return top_types[0]

def dedupe_degrees(degrees):
    unique = {}
    for d in degrees:
        key = (
            (d.get("context") or "").strip().lower(),
            (d.get("name") or "").strip().lower(),
            d.get("price"),
            (d.get("currency") or "").strip().upper(),
            (d.get("course_type") or "general").strip().lower(),
        )
        #if key not in unique:
        #    unique[key] = d

        if key not in unique:
            if not d.get("course_type"):
                d["course_type"] = "general"
            unique[key] = d

    return list(unique.values())

def degree_key(d):
    return (
        (d.get("context") or "").strip().lower(),
        (d.get("name") or "").strip().lower(),
        d.get("price"),
        (d.get("currency") or "").strip().upper(),
        (d.get("course_type") or "general").strip().lower(),
    )

def analyze_degree_duplicates(records):
    groups = {}
    total = 0
    for record in records:
        for d in record.get("degrees", []):
            total += 1
            key = degree_key(d)
            groups[key] = groups.get(key, 0) + 1

    dup_groups = 0
    dup_items = 0
    for count in groups.values():
        if count > 1:
            dup_groups += 1
            dup_items += count

    return {
        "degree_total": total,
        "degree_unique": len(groups),
        "degree_dup_groups": dup_groups,
        "degree_dup_items": dup_items,
    }


def classify_tuition(line_lower):
    """学費表記を固定コードで分類し、月額換算ルールを返す。"""
    if re.search(r"[0-9].{0,10}(\-|–|to).{0,10}[0-9]", line_lower):
        return "range", None, "range_not_normalized"
    if "per credit" in line_lower or "/credit" in line_lower:
        return "per_credit", None, "per_credit_not_normalized"
    if "per course" in line_lower or "/course" in line_lower:
        return "per_course", None, "per_course_not_normalized"
    if "per month" in line_lower or "/month" in line_lower or "monthly" in line_lower:
        return "fixed_month", 1, "monthly_as_is"
    if "per semester" in line_lower or "/semester" in line_lower:
        return "fixed_semester", 6, "semester_div_6"
    if "per year" in line_lower or "/year" in line_lower or "annual" in line_lower:
        return "fixed_year", 12, "yearly_div_12"
    if "total" in line_lower or "full program" in line_lower:
        return "total", None, "total_not_normalized"
    return "unknown", None, "unknown_not_normalized"


def detect_range_values(line):
    numbers = re.findall(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})", line)
    if len(numbers) < 2:
        return None, None
    values = [int(x.replace(",", "")) for x in numbers]
    # 年度（2000〜2099）と思われる数値は除外
    values = [v for v in values if not (2000 <= v <= 2099)]
    if len(values) < 2:
        return None, None
    return min(values), max(values)

def extract_info(self, text, title="", heading_text="", tag_hits=None):
    degree_keywords = ["bachelor", "master", "phd", "doctorate", "undergraduate", "graduate", "degree", "学士", "修士", "博士", "学位"]
    fee_keywords = [
        "tuition", "fee", "fees", "cost", "costs", "price", "prices", "funding",
        "international fee", "home fee", "uk fee", "overseas fee", "annual fee",
        "per year", "per semester", "per month", "full programme", "full program",
        "授業料", "費用", "学費",
    ]
    
    #degree_keywords = ["bachelor", "master", "phd", "doctorate", "undergraduate" "degree", "学士", "修士", "博士", "学位"]
    
    #degree_keywords = ["graduate", "program", "course", "study"]

    #カンマ付き数字も対応する価格抽出の正規表現　{1,3}は1000未満の数字、(?:,[0-9]{3})+はカンマと3桁の数字の繰り返し、|[0-9]{4,}は4桁以上の数字
    price_pattern = re.compile(r'(USD|EUR|GBP|JPY|AUD|CAD|\$|£|€|¥|円)?\s?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})')
    #
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    degrees = []

    for idx, line in enumerate(lines):
        line_lower = line.lower()
        if not any(keyword in line_lower for keyword in degree_keywords):
            continue
        self.extraction_drop_stats["degree_line_hits"] += 1
        #
        matched_keyword = next(keyword for keyword in degree_keywords if keyword in line_lower)
        price_match = price_pattern.search(line)
        price = None
        currency = None
        if price_match is not None:
            currency = price_match.group(1) or None
            price = int(price_match.group(2).replace(",", ""))

        tuition_type, divisor_month, normalization_note = classify_tuition(line_lower)
        amount_min, amount_max = detect_range_values(line)
        normalized_monthly_amount = None
        if price is not None and divisor_month:
            normalized_monthly_amount = round(price / divisor_month, 2)

        # 金額がある行でも学費文脈が薄いものは除外してノイズを減らす
        # ただし現在行だけでは取りこぼしが多いため、近傍行も確認する。
        left = max(0, idx - 2)
        right = min(len(lines), idx + 3)
        fee_context_text = " ".join(lines[left:right]).lower()
        has_fee_context = any(k in fee_context_text for k in fee_keywords)
        has_currency_marker = re.search(r"\b(usd|eur|gbp|jpy|aud|cad)\b|[\$£€¥円]", fee_context_text) is not None
        if price is not None and not has_fee_context and not has_currency_marker:
            self.extraction_drop_stats["dropped_fee_context"] += 1
            continue

        # 数字すらない学位行はノイズが多いため除外
        if price is None and not re.search(r"[0-9]", line):
            self.extraction_drop_stats["dropped_non_numeric"] += 1
            continue

        if currency == "$":
            currency = "USD"
        elif currency == "£":
            currency = "GBP"
        elif currency == "€":
            currency = "EUR"
        elif currency == "¥":
            currency = "JPY"
        elif currency == "円":
            currency = "JPY"

    #価格がない場合は残さない

        # 1行判定だと情報不足なので、前後行 + タイトルを含めた局所文脈を作る
        left = max(0, idx - 6)
        right = min(len(lines), idx + 7)
        local_context = " ".join(lines[left:right])
        primary_text = f"{title} {local_context}".strip()

        degrees.append({
            "name": matched_keyword.title(),
            "price": price,
            "currency": currency,
            "tuition_type": tuition_type,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "normalized_monthly_amount": normalized_monthly_amount,
            "normalization_note": normalization_note,
            "course_type": detect_course_type(primary_text, text, heading_text, tag_hits=tag_hits),
            "is_online": "online" in line_lower,
            "limit": "deadline" if "deadline" in line_lower else None,#期日などの締め切り情報があるかどうか
            "context": line,
        })

    for d in degrees:
        if d.get("price") is None:
            self.extraction_drop_stats["kept_without_price"] += 1
        else:
            self.extraction_drop_stats["kept_with_price"] += 1

    #長すぎるテキストは除外
    for d in degrees:
        if len(d["context"]) > 100:
            d["context"] = d["context"][:100] + "..."
    
    degrees = dedupe_degrees(degrees)

    #ルートURLを学位ページからにする方が良いかも

    return degrees

def extract_page(self,url, soup, page_text):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    raw_text = " ".join(page_text.split())
    # h1/h2/h3/dt タグのテキストを見出しとして抽出し、スコアリングで最大重みを与える
    heading_parts = [
        tag.get_text(" ", strip=True)
        for tag in soup.find_all(["h1", "h2", "h3", "dt"])
        if tag.get_text(strip=True)
    ]
    heading_text = " ".join(heading_parts)
    tag_keyword_hits = collect_tag_keyword_hits(soup)
    tag_class_counts = collect_tag_class_counts(soup)
    degrees = extract_info(self, page_text, title, heading_text, tag_keyword_hits)

    return {
        "url": url,
        "title": title,
        "country": guess_country_from_url(url),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "degrees": degrees,
        "tag_keyword_hits": tag_keyword_hits,
        "tag_class_counts": tag_class_counts,
        "raw_text": raw_text,
        "source_type": "html",
    }

def dedupe_records(records):
    unique = {}
    for record in records:
        record["degrees"] = dedupe_degrees(record.get("degrees", []))
        key = (canonicalize_url(record.get("url", "")), record.get("title", "").strip().lower())
        if key not in unique:
            unique[key] = record
            continue

        # 同一ページが重複した場合は学位配列を統合してから重複除去する
        prev = unique[key]
        merged_degrees = prev.get("degrees", []) + record.get("degrees", [])
        prev["degrees"] = dedupe_degrees(merged_degrees)

        # 情報量が増える場合のみ補足情報を更新
        if len(record.get("raw_text", "")) > len(prev.get("raw_text", "")):
            prev["raw_text"] = record.get("raw_text", "")
        if not prev.get("title") and record.get("title"):
            prev["title"] = record.get("title")
    return list(unique.values())



#--------------------------------
async def _fetch_with_requests(url: str, timeout: int, headers: dict[str, str]) -> requests.Response:
    def _get() -> requests.Response:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response

    return await asyncio.to_thread(_get)


def _extract_status_code(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _pick_fetch_method(connection_log: list[dict[str, Any]], used_fallback: bool = False) -> str:
    if used_fallback:
        return "requests"
    for entry in connection_log:
        client = entry.get("client")
        if client == "httpx" and entry.get("ok"):
            return "httpx"
    if connection_log:
        return str(connection_log[-1].get("client") or "unknown")
    return "unknown"


def _extract_sitemap_directives(robots_text: str) -> list[str]:
    sitemap_urls = []
    for line in robots_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("sitemap:"):
            sitemap_urls.append(stripped.split(":", 1)[1].strip())
    return sitemap_urls


def _collect_sitemap_urls(
    xml_text: str,
    allowed_netloc: str,
    visited_sitemaps: set[str],
    *,
    started_at: float,
    max_duration_sec: int,
    max_sitemaps: int,
) -> list[str]:
    root = ET.fromstring(xml_text)
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    ns = root.tag[: root.tag.rfind("}") + 1] if "}" in root.tag else ""

    if tag == "urlset":
        urls = []
        for url_elem in root.findall(f"{ns}url"):
            loc_elem = url_elem.find(f"{ns}loc")
            if loc_elem is not None and loc_elem.text:
                loc = loc_elem.text.strip()
                if urlparse(loc).netloc == allowed_netloc:
                    urls.append(loc)
        return urls

    if tag == "sitemapindex":
        urls = []
        for sitemap_elem in root.findall(f"{ns}sitemap"):
            if time.perf_counter() - started_at >= max_duration_sec:
                break
            if len(visited_sitemaps) >= max_sitemaps:
                break

            loc_elem = sitemap_elem.find(f"{ns}loc")
            if loc_elem is None or not loc_elem.text:
                continue
            sitemap_url = loc_elem.text.strip()
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)
            try:
                response = requests.get(
                    sitemap_url,
                    headers=DEFAULT_HEADERS,
                    timeout=REQUESTS_SITEMAP_TIMEOUT_SEC,
                )
                response.raise_for_status()
                urls.extend(
                    _collect_sitemap_urls(
                        response.text,
                        allowed_netloc,
                        visited_sitemaps,
                        started_at=started_at,
                        max_duration_sec=max_duration_sec,
                        max_sitemaps=max_sitemaps,
                    )
                )
            except Exception:
                continue
        return urls

    return []


def _select_likely_sitemap_candidates(urls: list[str], max_candidates: int) -> list[str]:
    candidates = []
    seen = set()
    for url in urls:
        lowered = url.lower()
        if url not in seen and any(keyword in lowered for keyword in SITEMAP_PRIORITY_KEYWORDS):
            candidates.append(url)
            seen.add(url)
            if len(candidates) >= max_candidates:
                break
    return candidates


async def seed_sitemap_candidates(site: SiteState) -> list[str]:
    if site.sitemap_attempted:
        return site.sitemap_candidates

    site.sitemap_attempted = True
    robots_url = f"https://{site.domain}/robots.txt"
    default_sitemaps = [f"https://{site.domain}/sitemap.xml"]

    def _load_candidates() -> list[str]:
        started_at = time.perf_counter()
        truncated_by_time = False
        truncated_by_count = False

        sitemap_urls: list[str] = []
        try:
            robots_resp = requests.get(
                robots_url,
                headers=DEFAULT_HEADERS,
                timeout=REQUESTS_ROBOTS_TIMEOUT_SEC,
            )
            if robots_resp.status_code == 200:
                sitemap_urls.extend(_extract_sitemap_directives(robots_resp.text))
        except Exception:
            pass

        if not sitemap_urls:
            sitemap_urls.extend(default_sitemaps)

        site.sitemap_urls = list(dict.fromkeys(sitemap_urls))

        collected_urls = []
        visited_sitemaps = set(site.sitemap_urls)
        for sitemap_url in site.sitemap_urls:
            if time.perf_counter() - started_at >= SITEMAP_SEED_MAX_SECONDS:
                truncated_by_time = True
                break
            if len(visited_sitemaps) >= SITEMAP_SEED_MAX_SITEMAPS:
                truncated_by_count = True
                break

            try:
                response = requests.get(
                    sitemap_url,
                    headers=DEFAULT_HEADERS,
                    timeout=REQUESTS_SITEMAP_TIMEOUT_SEC,
                )
                response.raise_for_status()
                collected_urls.extend(
                    _collect_sitemap_urls(
                        response.text,
                        site.domain,
                        visited_sitemaps,
                        started_at=started_at,
                        max_duration_sec=SITEMAP_SEED_MAX_SECONDS,
                        max_sitemaps=SITEMAP_SEED_MAX_SITEMAPS,
                    )
                )
                if time.perf_counter() - started_at >= SITEMAP_SEED_MAX_SECONDS:
                    truncated_by_time = True
                    break
                if len(visited_sitemaps) >= SITEMAP_SEED_MAX_SITEMAPS:
                    truncated_by_count = True
                    break
            except Exception:
                continue

        if truncated_by_time:
            print(
                f"[SITEMAP] timeout cap reached domain={site.domain} cap={SITEMAP_SEED_MAX_SECONDS}s",
                flush=True,
            )
        if truncated_by_count:
            print(
                f"[SITEMAP] sitemap-count cap reached domain={site.domain} cap={SITEMAP_SEED_MAX_SITEMAPS}",
                flush=True,
            )

        return _select_likely_sitemap_candidates(
            collected_urls,
            max_candidates=SITEMAP_SEED_MAX_CANDIDATES_PER_DOMAIN,
        )

    candidates = await asyncio.to_thread(_load_candidates)
    site.sitemap_candidates = candidates
    for candidate_url in candidates:
        if should_queue_url(site, candidate_url):
            site.enqueue(candidate_url, depth=0, discovered_from="sitemap")
    return site.sitemap_candidates


async def fetch_with_fallback(
    session: httpx.AsyncClient,
    url: str,
    *,
    timeout: int = REQUESTS_TIMEOUT_SEC,
    headers: Optional[dict[str, str]] = None,
) -> FetchResult:
    """httpx 失敗時は必ず requests を実行する。"""
    #*,は以降の引数をキーワード指定必須にする宣言
    request_headers = headers or DEFAULT_HEADERS
    connection_log: list[dict[str, Any]] = []
    try:
        response = await session.get(url, follow_redirects=True)
        response.raise_for_status()
        connection_log.append(
            {
                "client": "httpx",
                "ok": True,
                "status_code": response.status_code,
                "final_url": str(response.url),
            }
        )
        return FetchResult(
            html_text=response.text,
            status_code=response.status_code,
            used_fallback=False,
            connection_log=connection_log,
        )
    except Exception as httpx_exc:
        connection_log.append(
            {
                "client": "httpx",
                "ok": False,
                "status_code": _extract_status_code(httpx_exc),
                "error": f"{type(httpx_exc).__name__}: {httpx_exc}",
            }
        )
        try:
            fallback_response = await _fetch_with_requests(url, timeout=timeout, headers=request_headers)
            connection_log.append(
                {
                    "client": "requests",
                    "ok": True,
                    "status_code": fallback_response.status_code,
                    "final_url": str(fallback_response.url),
                }
            )
            return FetchResult(
                html_text=fallback_response.text,
                status_code=fallback_response.status_code,
                used_fallback=True,
                connection_log=connection_log,
            )
        except Exception as requests_exc:
            connection_log.append(
                {
                    "client": "requests",
                    "ok": False,
                    "status_code": _extract_status_code(requests_exc),
                    "error": f"{type(requests_exc).__name__}: {requests_exc}",
                }
            )
            raise FetchWithFallbackError(
                message=str(requests_exc),
                connection_log=connection_log,
                status_code=_extract_status_code(requests_exc),
            ) from requests_exc


async def ensure_robots(site: SiteState) -> None:
    if site.robots_ready:
        return

    robot_url = f"https://{site.domain}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robot_url)

    def _read() -> None:
        parser.read()

    try:
        await asyncio.wait_for(asyncio.to_thread(_read), timeout=ROBOTS_READ_TIMEOUT_SEC)
        crawl_delay = parser.crawl_delay(site.user_agent) or parser.crawl_delay("*")
        if crawl_delay:
            site.crawl_delay = max(site.crawl_delay, float(crawl_delay))
        site.robots_parser = parser
    except Exception:
        site.robots_parser = None
    finally:
        site.robots_ready = True


async def run_url_task(site: SiteState, task: URLTask, session: httpx.AsyncClient) -> CrawlAttempt:
    """URLTask を 1 件処理し、CrawlAttempt として結果を返す。"""
    site.start_task(task)

    if site.queue_logger is not None and site.run_id is not None:
        try:
            site.queue_logger.upsert_queue_state(
                run_id=site.run_id,
                url=task.url,
                parent_url=task.discovered_from,
                domain=site.domain,
                depth=task.depth,
                status="processing",
                discovered_from=task.discovered_from,
                retry_count=task.retry_count,
                set_started=True,
            )
        except Exception:
            pass

    if site.robots_parser and not site.robots_parser.can_fetch(site.user_agent, task.url):
        attempt = CrawlAttempt(
            url=task.url,
            ok=False,
            task_depth=task.depth,
            error="blocked_by_robots",
        )
        site.record_attempt(attempt, elapsed_sec=0.0)
        site.add_error(f"{task.url}: blocked_by_robots")

        if site.queue_logger is not None and site.run_id is not None:
            try:
                site.queue_logger.upsert_queue_state(
                    run_id=site.run_id,
                    url=task.url,
                    parent_url=task.discovered_from,
                    domain=site.domain,
                    depth=task.depth,
                    status="error",
                    discovered_from=task.discovered_from,
                    retry_count=task.retry_count,
                    fetch_method="robots",
                    last_error_type="RobotsBlocked",
                    last_error_message="blocked_by_robots",
                    set_finished=True,
                )
                site.queue_logger.add_attempt(
                    run_id=site.run_id,
                    url=task.url,
                    fetch_method="robots",
                    ok=False,
                    status_code=None,
                    error_type="RobotsBlocked",
                    error_message="blocked_by_robots",
                    final_url=task.url,
                    response_bytes=None,
                    used_fallback=False,
                    connection_log=[],
                )
            except Exception:
                pass

        site.finish_task(task.url)
        return attempt

    start = time.perf_counter()
    try:
        fetch_result = await fetch_with_fallback(session, task.url)
        html_text = fetch_result.html_text
        status_code = fetch_result.status_code
        used_fallback = fetch_result.used_fallback
        connection_log = fetch_result.connection_log
        if bot_check_page(html_text):
            site.add_error(f"{task.url}: bot_check_suspected")

        links = extract_same_domain_links(html_text, task.url, site.domain)
        soup = BeautifulSoup(html_text, "html.parser")
        page_text = soup.get_text(separator=" ", strip=True)

        extracted_records = []
        if check_keywords_in_text(page_text.lower(), [k.lower() for k in keywords]):
            page_record = extract_page(site, task.url, soup, page_text)
            if site.queue_logger is not None and site.run_id is not None:
                try:
                    site.queue_logger.add_tag_keyword_hits(
                        run_id=site.run_id,
                        url=task.url,
                        domain=site.domain,
                        tag_hits=page_record.get("tag_keyword_hits", []),
                    )
                    if _should_log_tag_class_counts(site, task.url):
                        site.queue_logger.add_tag_class_counts(
                            run_id=site.run_id,
                            url=task.url,
                            domain=site.domain,
                            class_counts=page_record.get("tag_class_counts", []),
                        )
                except Exception:
                    pass
            if not should_exclude_course_page(task.url, page_record.get("title", "")):
                if page_record.get("degrees"):
                    await site.add_extracted_record(page_record)
                    extracted_records.append(page_record)

        site.record_links(task.url, links)
        queued_urls = []
        for link in links:
            if should_queue_url(site, link):
                if site.enqueue(url=link, depth=task.depth + 1, discovered_from=task.url):
                    queued_urls.append(link)

        attempt = CrawlAttempt(
            url=task.url,
            ok=True,
            task_depth=task.depth,
            status_code=status_code,
            used_fallback=used_fallback,
            discovered_urls=len(links),
            queued_urls=queued_urls,
            extracted_records=extracted_records,
            connection_log=connection_log,
        )
        site.record_attempt(attempt, elapsed_sec=time.perf_counter() - start)

        if site.queue_logger is not None and site.run_id is not None:
            fetch_method = _pick_fetch_method(connection_log, used_fallback=used_fallback)
            try:
                site.queue_logger.upsert_queue_state(
                    run_id=site.run_id,
                    url=task.url,
                    parent_url=task.discovered_from,
                    domain=site.domain,
                    depth=task.depth,
                    status="done",
                    discovered_from=task.discovered_from,
                    retry_count=task.retry_count,
                    fetch_method=fetch_method,
                    status_code=status_code,
                    set_finished=True,
                )
                site.queue_logger.add_attempt(
                    run_id=site.run_id,
                    url=task.url,
                    fetch_method=fetch_method,
                    ok=True,
                    status_code=status_code,
                    error_type="",
                    error_message="",
                    final_url=task.url,
                    response_bytes=len(html_text),
                    used_fallback=used_fallback,
                    connection_log=connection_log,
                )
            except Exception:
                pass

        site.finish_task(task.url)
        return attempt
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        connection_log = getattr(exc, "connection_log", [])
        attempt = CrawlAttempt(
            url=task.url,
            ok=False,
            task_depth=task.depth,
            status_code=status_code,
            error=str(exc),
            connection_log=connection_log,
        )
        site.record_attempt(attempt, elapsed_sec=time.perf_counter() - start)
        site.add_error(f"{task.url}: {type(exc).__name__}: {exc}")

        if site.queue_logger is not None and site.run_id is not None:
            fetch_method = _pick_fetch_method(connection_log, used_fallback=False)
            try:
                site.queue_logger.upsert_queue_state(
                    run_id=site.run_id,
                    url=task.url,
                    parent_url=task.discovered_from,
                    domain=site.domain,
                    depth=task.depth,
                    status="error",
                    discovered_from=task.discovered_from,
                    retry_count=task.retry_count,
                    fetch_method=fetch_method,
                    status_code=status_code,
                    last_error_type=type(exc).__name__,
                    last_error_message=str(exc),
                    set_finished=True,
                )
                site.queue_logger.add_attempt(
                    run_id=site.run_id,
                    url=task.url,
                    fetch_method=fetch_method,
                    ok=False,
                    status_code=status_code,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    final_url=task.url,
                    response_bytes=None,
                    used_fallback=False,
                    connection_log=connection_log,
                )
            except Exception:
                pass

        site.finish_task(task.url)
        return attempt


async def worker(site: SiteState, session: httpx.AsyncClient) -> Optional[CrawlAttempt]:
    """1回分の探索を実行する。ready でない場合は None を返す。"""
    if not site.has_pending() or not site.can_fetch():
        return None

    await ensure_robots(site)
    task = site.pop_next_task()
    if task is None:
        return None

    return await run_url_task(site, task, session)
