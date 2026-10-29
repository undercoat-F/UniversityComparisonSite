-- 取得の直前に、ドメインのアクセス枠を予約する（domain_rate_limiter.py の RESERVE_SCRIPT）。
-- Valkey はスクリプトを1回の操作として実行するため、複数の Worker が同時に予約しても枠は重ならない。
--
-- KEYS[1] = ドメインのキー（crawl:next:<ドメイン>）。値は「次にアクセスしてよい時刻」（ミリ秒）
-- ARGV[1] = Crawl-delay（ミリ秒）
-- ARGV[2] = 待ちのないキーを残す時間（ミリ秒）
-- 戻り値  = 待つべき時間（ミリ秒）

-- Worker 間の時計のずれの影響を受けないよう、Valkey サーバーの時計を使う（TIME は {秒, マイクロ秒}）
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local stored = tonumber(redis.call('GET', KEYS[1]) or '0')
local start = math.max(now, stored)          -- 自分がアクセスしてよい時刻
local next_at = start + tonumber(ARGV[1])    -- 次の人がアクセスしてよい時刻
redis.call('SET', KEYS[1], next_at, 'PX', (next_at - now) + tonumber(ARGV[2]))
return start - now
