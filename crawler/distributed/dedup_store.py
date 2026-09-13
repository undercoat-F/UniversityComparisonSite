from __future__ import annotations

import hashlib
import os

from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

try:
    import redis
except ImportError:  # pragma: no cover - redis is optional until wired in
    redis = None

DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30日


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _url_key(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"seen:{digest}"


class DedupStore:
    """Redisを使った分散ワーカー間のURL重複排除。"""

    def __init__(self, redis_url: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        if redis is None:
            raise RuntimeError("redis package is not installed. Install redis (pip install redis).")
        if not redis_url:
            raise ValueError("redis_url is required")
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._client = redis.from_url(redis_url, decode_responses=True)

    def mark_seen(self, url: str) -> bool:
        """未訪問なら登録してTrue、既訪問ならFalseを返す(アトミック判定)。"""
        return bool(self._client.set(_url_key(url), "1", nx=True, ex=self.ttl_seconds))

    def is_seen(self, url: str) -> bool:
        """登録せずに既訪問かどうかだけを確認する。"""
        return bool(self._client.exists(_url_key(url)))

    def forget(self, url: str) -> None:
        """テストやリトライ用に登録を取り消す。"""
        self._client.delete(_url_key(url))

    def close(self) -> None:
        self._client.close()


_default_store: DedupStore | None = None


def get_dedup_store() -> DedupStore:
    """環境変数(REDIS_URL / DEDUP_TTL_SECONDS)から既定のDedupStoreを取得する。"""
    global _default_store
    if _default_store is None:
        redis_url = os.getenv("REDIS_URL", "").strip()
        ttl_seconds = _env_int("DEDUP_TTL_SECONDS", DEFAULT_TTL_SECONDS)
        _default_store = DedupStore(redis_url=redis_url, ttl_seconds=ttl_seconds)
    return _default_store
