"""ベンチマーク用Mockシナリオの読み込みと展開。

シナリオYAMLを読み込み、仮想ドメイン・ページ・応答内容を決定的に展開する。
Mock Server (server.py) と内容確認ツール (inspect_scenario.py) は必ずこのモジュールを使い、
確認ツールで見た内容と実際の応答が一致するようにする。
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any, Optional
from xml.sax.saxutils import escape

import yaml

TEMPLATE_DIR = Path(__file__).parent / "templates"

# robots.txt / sitemap.xml の既定応答。response_types に定義がなくても使える。
BUILTIN_RESPONSE_TYPES = {"ok": {"status": 200, "delay_ms": 0}}

DEFAULT_PROFILE: dict[str, Any] = {
    "crawl_delay": 1,           # 整数秒　0 のとき robots.txt に Crawl-delay を書かない
    "pages": 50,                # ルート "/" を除くページ数
    "links_per_page": 5,        # ページツリーの分岐数（各ページが持つ子ページへのリンク数）
    "extra_links": 0,           # ツリー外のランダムな同一ドメイン内リンク数
    "sitemap_ratio": 1.0,       # sitemap.xml に載せるページの割合
    "paragraphs": 4,            # 本文の段落数（ページサイズの調整用）
    "responses": {"fast": 1.0}, # ページごとの応答種別の比率
    "root_response": "fast",    # ルート "/" の応答種別
    "robots": "ok",             # robots.txt の応答種別
    "sitemap": "ok",            # sitemap.xml の応答種別
    "disallow": [],             # robots.txt の Disallow パス
}
PROFILE_KEYS = set(DEFAULT_PROFILE)

DEFAULT_CHECKS = {
    "crawler_max_depth": 5,            # クローラーの ETL_WORKER_MAX_DEPTH
    "client_timeout_sec": 30,          # クローラー側のタイムアウト（requests フォールバック側）
    "min_theoretical_throughput": 0,   # 理論上限がこれを下回ると警告（URLs/sec）
}

DEGREES = [
    "Bachelor of Science", "Bachelor of Arts", "Bachelor of Engineering",
    "Master of Science", "Master of Arts", "Master of Engineering", "PhD",
]
SUBJECTS = [
    "Computer Science", "Mechanical Engineering", "Economics", "Mathematics",
    "Physics", "Chemistry", "Biology", "History", "Linguistics", "Architecture",
    "Data Science", "Civil Engineering", "Psychology", "Philosophy", "Statistics",
]
CURRENCIES = [("GBP", "£"), ("USD", "$"), ("EUR", "€"), ("AUD", "$")]
FILLER = [
    "Students develop both theoretical understanding and practical skills through lectures, seminars and project work.",
    "The curriculum is reviewed every year together with industry partners and research groups.",
    "Teaching takes place on the main campus, with laboratory sessions in dedicated facilities.",
    "Assessment combines written examinations, coursework and a final individual project.",
    "Optional modules allow students to specialise in areas that match their interests.",
    "Graduates work in industry, the public sector and research institutions around the world.",
    "Small group tutorials give every student regular feedback from academic staff members.",
    "A placement year can be added between the second and third year of the programme.",
]


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class ResponseType:
    name: str
    status: Optional[int]  # None: 応答を返さず、クライアントが切断するまで（最大 delay_ms）保持する
    delay_ms: int
    jitter_ms: int = 0     # ページごとに 0〜jitter_ms を加算（ページ単位で固定、実行ごとに同じ）


@dataclass
class Page:
    path: str
    index: int              # 0 = ルート
    response: str
    delay_ms: int
    tree_depth: int
    links: list[str] = field(default_factory=list)
    in_sitemap: bool = False


@dataclass
class Domain:
    host: str
    profile: str
    settings: dict[str, Any]
    pages: dict[str, Page] = field(default_factory=dict)

    @property
    def crawl_delay(self) -> int:
        return int(self.settings["crawl_delay"])

    @property
    def max_tree_depth(self) -> int:
        return max(p.tree_depth for p in self.pages.values())


class Scenario:
    def __init__(self, raw: dict[str, Any], source: Optional[Path] = None) -> None:
        self.raw = raw
        self.source = source
        self.name = str(raw.get("scenario") or (source.stem if source else "scenario"))
        self.seed = raw.get("seed", 0)
        self.scheme = str(raw.get("scheme", "https"))
        self.suffix = str(raw.get("domain_suffix", "bench.internal")).strip(".")
        self.checks = {**DEFAULT_CHECKS, **(raw.get("checks") or {})}
        self.response_types = self._load_response_types(raw.get("response_types") or {})
        self.profiles = self._resolve_profiles(raw.get("profiles") or {})
        self.domains: dict[str, Domain] = {}
        for host, profile_name, overrides in self._plan_domains():
            settings = {**self.profiles[profile_name], **overrides}
            self._validate_settings(settings, f"domain {host}")
            domain = Domain(host=host, profile=profile_name, settings=settings)
            self._build_pages(domain)
            self.domains[host] = domain

    # ---------- 読み込み・検証 ----------

    def _load_response_types(self, raw: dict[str, Any]) -> dict[str, ResponseType]:
        merged = {**BUILTIN_RESPONSE_TYPES, **raw}
        types = {}
        for name, spec in merged.items():
            status = spec.get("status")
            if isinstance(status, str) and status.lower() == "none":
                status = None
            types[name] = ResponseType(
                name=name,
                status=None if status is None else int(status),
                delay_ms=int(spec.get("delay_ms", 0)),
                jitter_ms=int(spec.get("jitter_ms", 0)),
            )
        return types

    def _resolve_profiles(self, raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}

        def resolve(name: str, chain: tuple[str, ...]) -> dict[str, Any]:
            if name in resolved:
                return resolved[name]
            if name not in raw:
                raise ScenarioError(f"profile '{name}' is not defined")
            if name in chain:
                raise ScenarioError(f"profile inheritance cycle: {' -> '.join(chain + (name,))}")
            spec = dict(raw[name] or {})
            parent = spec.pop("inherit", None)
            unknown = set(spec) - PROFILE_KEYS
            if unknown:
                raise ScenarioError(f"profile '{name}' has unknown keys: {sorted(unknown)}")
            base = resolve(parent, chain + (name,)) if parent else DEFAULT_PROFILE
            resolved[name] = {**base, **spec}
            return resolved[name]

        for name in raw:
            resolve(name, ())
        for name, settings in resolved.items():
            self._validate_settings(settings, f"profile {name}")
        return resolved

    def _validate_settings(self, s: dict[str, Any], where: str) -> None:
        if int(s["crawl_delay"]) != s["crawl_delay"] or s["crawl_delay"] < 0:
            raise ScenarioError(f"{where}: crawl_delay must be a non-negative integer (R1.5)")
        if s["pages"] < 0 or s["links_per_page"] < 1:
            raise ScenarioError(f"{where}: pages >= 0 and links_per_page >= 1 are required")
        if not 0.0 <= float(s["sitemap_ratio"]) <= 1.0:
            raise ScenarioError(f"{where}: sitemap_ratio must be within 0.0-1.0")
        names = list(s["responses"]) + [s["root_response"], s["robots"], s["sitemap"]]
        for name in names:
            if name not in self.response_types:
                raise ScenarioError(f"{where}: response type '{name}' is not defined")
        _check_weights(s["responses"], f"{where}: responses")

    def _host(self, name: str) -> str:
        return name if "." in name else f"{name}.{self.suffix}"

    def _plan_domains(self) -> list[tuple[str, str, dict[str, Any]]]:
        raw = self.raw
        modes = [k for k in ("mix", "counts", "domains") if raw.get(k)]
        if len(modes) != 1:
            raise ScenarioError("specify exactly one of: total_domains + mix / counts / domains")

        if modes[0] == "domains":
            planned = []
            for i, item in enumerate(raw["domains"]):
                item = dict(item)
                name = item.pop("name", None) or f"d{i + 1:03d}"
                profile = item.pop("profile", None)
                if profile not in self.profiles:
                    raise ScenarioError(f"domain {name}: profile '{profile}' is not defined")
                unknown = set(item) - PROFILE_KEYS
                if unknown:
                    raise ScenarioError(f"domain {name} has unknown keys: {sorted(unknown)}")
                planned.append((self._host(name), profile, item))
            hosts = [h for h, _, _ in planned]
            if len(set(hosts)) != len(hosts):
                raise ScenarioError("duplicate domain names in 'domains'")
            return planned

        if modes[0] == "mix":
            total = int(raw.get("total_domains") or 0)
            if total <= 0:
                raise ScenarioError("total_domains is required with mix")
            counts = allocate(total, raw["mix"], "mix")
        else:
            counts = {k: int(v) for k, v in raw["counts"].items()}
        for profile in counts:
            if profile not in self.profiles:
                raise ScenarioError(f"profile '{profile}' is not defined")

        assigned = [p for p, n in counts.items() for _ in range(n)]
        # 種別がドメイン番号順に偏らないよう、seed固定でシャッフルする
        random.Random(f"{self.seed}:domains").shuffle(assigned)
        width = max(3, len(str(len(assigned))))
        return [(self._host(f"d{i + 1:0{width}d}"), p, {}) for i, p in enumerate(assigned)]

    # ---------- ページ展開 ----------

    def _build_pages(self, domain: Domain) -> None:
        s = domain.settings
        rng = random.Random(f"{self.seed}:{domain.host}")
        n = int(s["pages"])
        branch = int(s["links_per_page"])

        responses = [r for r, c in allocate(n, s["responses"], "responses").items() for _ in range(c)]
        rng.shuffle(responses)
        sitemap_count = round(n * float(s["sitemap_ratio"]))
        in_sitemap = set(rng.sample(range(1, n + 1), sitemap_count)) if n else set()

        paths = ["/"] + [f"/programs/{_slug(domain.host, i)}-{i:04d}" for i in range(1, n + 1)]
        depths = [0] * (n + 1)
        for i in range(1, n + 1):
            depths[i] = depths[(i - 1) // branch] + 1

        for i, path in enumerate(paths):
            rtype = s["root_response"] if i == 0 else responses[i - 1]
            rt = self.response_types[rtype]
            delay = rt.delay_ms + (rng.randint(0, rt.jitter_ms) if rt.jitter_ms else 0)
            # 子ページ（ツリー）: i*branch+1 .. i*branch+branch。全ページがルートから到達可能になる
            children = [c for c in range(i * branch + 1, i * branch + branch + 1) if c <= n]
            extra = [c for c in rng.sample(range(1, n + 1), min(n, int(s["extra_links"]))) if c != i] if n else []
            links = [paths[c] for c in dict.fromkeys(children + extra)]
            domain.pages[path] = Page(
                path=path, index=i, response=rtype, delay_ms=delay,
                tree_depth=depths[i], links=links, in_sitemap=i in in_sitemap,
            )

    # ---------- 参照 ----------

    def url(self, host: str, path: str = "/") -> str:
        return f"{self.scheme}://{host}{path}"

    def resolve(self, host: str, path: str) -> Optional[tuple[ResponseType, int, str, str]]:
        """(応答種別, 遅延ms, content-type, 本文) を返す。未定義の host/path は None。"""
        domain = self.domains.get(host)
        if domain is None:
            return None
        if path == "/robots.txt":
            rt = self.response_types[domain.settings["robots"]]
            return rt, rt.delay_ms, "text/plain; charset=utf-8", self.render_robots(host)
        if path == "/sitemap.xml":
            rt = self.response_types[domain.settings["sitemap"]]
            return rt, rt.delay_ms, "application/xml; charset=utf-8", self.render_sitemap(host)
        page = domain.pages.get(path)
        if page is None:
            return None
        return self.response_types[page.response], page.delay_ms, "text/html; charset=utf-8", self.render_page(host, path)

    @lru_cache(maxsize=None)
    def render_robots(self, host: str) -> str:
        d = self.domains[host]
        crawl_delay = f"Crawl-delay: {d.crawl_delay}\n" if d.crawl_delay > 0 else ""
        disallow = "".join(f"Disallow: {p}\n" for p in d.settings["disallow"])
        return _template("robots.txt").substitute(
            crawl_delay=crawl_delay, disallow=disallow, sitemap_url=self.url(host, "/sitemap.xml")
        )

    @lru_cache(maxsize=None)
    def render_sitemap(self, host: str) -> str:
        pages = [p for p in self.domains[host].pages.values() if p.in_sitemap]
        urls = "".join(f"  <url><loc>{escape(self.url(host, p.path))}</loc></url>\n" for p in pages)
        return _template("sitemap.xml").substitute(urls=urls)

    @lru_cache(maxsize=None)
    def render_page(self, host: str, path: str) -> str:
        domain = self.domains[host]
        page = domain.pages[path]
        rt = self.response_types[page.response]
        institution = f"{host.split('.')[0].upper()} University"
        rng = random.Random(f"{self.seed}:{host}:{path}")
        paragraphs = int(domain.settings["paragraphs"])

        if rt.status is not None and rt.status >= 400:
            title = f"{rt.status} - {institution}"
            heading = f"Error {rt.status}"
            body = "<p>The server could not complete the request.</p>"
        elif page.index == 0:
            title = f"{institution} - Programmes"
            heading = f"Programmes at {institution}"
            body = "\n".join(f"<p>{escape(rng.choice(FILLER))}</p>" for _ in range(paragraphs))
        else:
            degree = rng.choice(DEGREES)
            subject = rng.choice(SUBJECTS)
            years = rng.choice([1, 2, 3, 4])
            code, symbol = rng.choice(CURRENCIES)
            fee = rng.randrange(8000, 40000, 250)
            title = f"{degree} in {subject} - {institution}"
            heading = f"{degree} in {subject}"
            lines = [
                f"<p>The {degree} in {subject} is a {years}-year full-time programme.</p>",
                "<h2>Key information</h2>",
                "<ul>",
                f"<li>Degree: {degree} ({subject})</li>",
                f"<li>Duration: {years} years full-time</li>",
                f"<li>Tuition fee (international students): {symbol}{fee:,} {code} per year</li>",
                "</ul>",
            ]
            lines += [f"<p>{escape(rng.choice(FILLER))}</p>" for _ in range(paragraphs)]
            body = "\n".join(lines)

        links = "\n".join(
            f'<li><a href="{escape(link)}">{escape(_link_label(link))}</a></li>' for link in page.links
        )
        return _template("page.html").substitute(title=escape(title), heading=escape(heading), body=body, links=links)

    # ---------- 集計 ----------

    def reachable_pages(self, domain: Domain) -> set[str]:
        """ルートと sitemap 掲載ページから、200 系ページのリンクをたどって到達できるページ。

        robots.txt の Disallow と、クローラーの深さ制限は考慮しない。
        """
        pages = domain.pages
        start = ["/"] + ([p.path for p in pages.values() if p.in_sitemap] if self._sitemap_ok(domain) else [])
        seen: set[str] = set()
        queue = deque(start)
        while queue:
            path = queue.popleft()
            if path in seen:
                continue
            seen.add(path)
            status = self.response_types[pages[path].response].status
            if status is not None and 200 <= status < 300:
                queue.extend(l for l in pages[path].links if l not in seen)
        return seen

    def _sitemap_ok(self, domain: Domain) -> bool:
        status = self.response_types[domain.settings["sitemap"]].status
        return status is not None and 200 <= status < 300

    def seed_urls(self) -> list[str]:
        return [self.url(host) for host in self.domains]


def _check_weights(weights: dict[str, float], where: str) -> None:
    if not weights:
        raise ScenarioError(f"{where}: at least one entry is required")
    if any(float(w) < 0 for w in weights.values()):
        raise ScenarioError(f"{where}: weights must be non-negative")
    if not math.isclose(sum(float(w) for w in weights.values()), 1.0, abs_tol=1e-6):
        raise ScenarioError(f"{where}: weights must sum to 1.0 (got {sum(weights.values())})")


def allocate(total: int, weights: dict[str, float], where: str) -> dict[str, int]:
    """比率を件数に変換する（最大剰余法）。同率の端数は記載順を優先する。"""
    _check_weights(weights, where)
    exact = {k: total * float(w) for k, w in weights.items()}
    counts = {k: math.floor(v) for k, v in exact.items()}
    remainder = total - sum(counts.values())
    order = sorted(exact, key=lambda k: -(exact[k] - counts[k]))
    for k in order[:remainder]:
        counts[k] += 1
    return counts


def _slug(host: str, index: int) -> str:
    # URL に URL_Filter の除外語（news, event, about 等）を含めないよう、固定の語だけを使う
    return ["course", "degree", "study"][(index + len(host)) % 3]


def _link_label(path: str) -> str:
    return "Home" if path == "/" else path.rsplit("/", 1)[-1].replace("-", " ").title()


@lru_cache(maxsize=None)
def _template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Scenario(raw, source=path)
