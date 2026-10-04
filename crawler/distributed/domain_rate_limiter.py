"""同一ドメインへのアクセス間隔（Crawl-delay）を Worker 間で守るための予約処理。

Valkey にドメインごとの「次にアクセスしてよい時刻」を持たせ、取得の直前に枠を予約する。

    start   = max(今, 保存されている次回時刻)   ← 自分がアクセスしてよい時刻
    保存値  = start + Crawl-delay              ← 次の人がアクセスしてよい時刻
    戻り値  = start - 今                       ← 待つ時間

- 予約は Lua スクリプトで1回の操作として実行するため、複数の Worker が同時に予約しても枠は重ならない
- 時刻は Valkey サーバーの時計（TIME）を使うため、Worker 間の時計のずれの影響を受けない
- 間隔はリクエストの開始時刻どうしで空ける（Legacy の SiteState.mark_access と同じ）
- Valkey が使えないときは Worker 内だけの予約に切り替える（Worker を複数台にすると間隔は保証されない）
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Optional

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

KEY_PREFIX = "crawl:next:"
# 最後の予約から、この時間アクセスがなければキーを消す（次回時刻を過ぎたキーは不要なため）
KEY_IDLE_TTL_MS = 10 * 60 * 1000

# KEYS[1] = ドメインのキー, ARGV[1] = Crawl-delay (ms), ARGV[2] = 待ちのないキーを残す時間 (ms)
RESERVE_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local stored = tonumber(redis.call('GET', KEYS[1]) or '0')
local start = math.max(now, stored)
local next_at = start + tonumber(ARGV[1])
redis.call('SET', KEYS[1], next_at, 'PX', (next_at - now) + tonumber(ARGV[2]))
return start - now
"""


class LocalDomainRateLimiter:
    """Worker 内だけで予約する。Valkey がない環境と、Valkey 障害時の代替に使う。"""

    def __init__(self) -> None:
        self._next_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def reserve_blocking(self, domain: str, delay_sec: float) -> float:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at.get(domain, 0.0))
            self._next_at[domain] = start + delay_sec
            return start - now

    async def reserve(self, domain: str, delay_sec: float) -> float:
        """待つべき秒数を返す。delay_sec が 0 以下なら予約しない。"""
        if delay_sec <= 0:
            return 0.0
        return self.reserve_blocking(domain, delay_sec)

    def close(self) -> None:
        return


class ValkeyDomainRateLimiter:
    def __init__(self, client, *, fallback: Optional[LocalDomainRateLimiter] = None) -> None:
        self._client = client
        self._script = client.register_script(RESERVE_SCRIPT)
        self._fallback = fallback or LocalDomainRateLimiter()
        self._fallback_warned = False

    def reserve_blocking(self, domain: str, delay_sec: float) -> float:
        wait_ms = self._script(keys=[KEY_PREFIX + domain], args=[int(round(delay_sec * 1000)), KEY_IDLE_TTL_MS])
        return max(0, int(wait_ms)) / 1000

    async def reserve(self, domain: str, delay_sec: float) -> float:
        """待つべき秒数を返す。delay_sec が 0 以下なら予約しない。"""
        if delay_sec <= 0:
            return 0.0
        try:
            return await asyncio.to_thread(self.reserve_blocking, domain, delay_sec)
        except Exception as exc:  # noqa: BLE001
            if not self._fallback_warned:
                print(
                    f"[RATE_LIMIT][WARN] Valkey reservation failed; using worker-local reservation: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                self._fallback_warned = True
            return await self._fallback.reserve(domain, delay_sec)

    def close(self) -> None:
        self._client.close()


def get_domain_rate_limiter():
    """REDIS_URL があれば Valkey で、なければ Worker 内だけで予約する limiter を返す。"""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or redis is None:
        print(
            "[RATE_LIMIT][WARN] REDIS_URL is not set; Crawl-delay is enforced only within this worker",
            flush=True,
        )
        return LocalDomainRateLimiter()
    try:
        return ValkeyDomainRateLimiter(redis.from_url(redis_url, decode_responses=True))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[RATE_LIMIT][WARN] could not initialize Valkey rate limiter; Crawl-delay is enforced only "
            f"within this worker: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return LocalDomainRateLimiter()
