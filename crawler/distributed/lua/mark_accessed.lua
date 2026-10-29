-- 取得の完了後に、次回時刻を「今（完了）+ Crawl-delay」より前にしないよう後ろ倒しする
-- （domain_rate_limiter.py の MARK_ACCESSED_SCRIPT）。
--
-- KEYS[1] = ドメインのキー（crawl:next:<ドメイン>）
-- ARGV[1] = Crawl-delay（ミリ秒）
-- ARGV[2] = 待ちのないキーを残す時間（ミリ秒）
-- 戻り値  = 次回時刻までの時間（ミリ秒）

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local stored = tonumber(redis.call('GET', KEYS[1]) or '0')
local next_at = math.max(stored, now + tonumber(ARGV[1]))
redis.call('SET', KEYS[1], next_at, 'PX', (next_at - now) + tonumber(ARGV[2]))
return next_at - now
