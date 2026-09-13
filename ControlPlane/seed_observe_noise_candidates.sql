-- seed_observe_results からノイズ候補を抽出するための SQL（削除はしない）
-- 目的:
-- 1) 観測結果(JSONB)に含まれる seed URL 候補を展開し、ノイズ疑いを理由つきで一覧化
-- 2) run_id / 理由別の件数を確認
--
-- 想定DB: PostgreSQL (Neon)

-- 参考: 検索APIに渡されたクエリ文字列を確認するSQL
-- 最新 run の query 一覧
SELECT
  r.run_id,
  r.id AS result_id,
  q.query_text,
  r.created_at
FROM seed_observe_results r
CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
WHERE r.run_id = (SELECT id FROM seed_observe_runs ORDER BY id DESC LIMIT 1)
ORDER BY r.id DESC, q.query_text;
--
-- run ごとの query 使用回数
SELECT
  r.run_id,
  q.query_text,
  count(*) AS used_count
FROM seed_observe_results r
CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
GROUP BY r.run_id, q.query_text
ORDER BY r.run_id DESC, used_count DESC, q.query_text;

-- run ごとの検索クエリ集計（実行単位の確認用）
SELECT
  r.run_id,
  count(*) AS result_count,
  sum(jsonb_array_length(coalesce(r.search_queries, '[]'::jsonb))) AS total_query_count,
  count(DISTINCT q.query_text) AS distinct_query_count,
  min(r.created_at) AS oldest_created_at,
  max(r.created_at) AS latest_created_at
FROM seed_observe_results r
LEFT JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
  ON true
GROUP BY r.run_id
ORDER BY r.run_id DESC;

-- 最新 run の検索クエリ一覧（上位確認用）
SELECT
  r.id AS result_id,
  q.query_text,
  r.source_domain,
  r.source_url,
  r.created_at
FROM seed_observe_results r
CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
WHERE r.run_id = (SELECT id FROM seed_observe_runs ORDER BY id DESC LIMIT 1)
ORDER BY r.id DESC, q.query_text;

-- =====================================================================
-- 1) ノイズ候補URL一覧（review用）
-- =====================================================================
WITH base AS (
    SELECT
        r.id AS result_id,
        r.run_id,
        r.external_run_id,
        r.source_stage,
        r.source_url,
        r.source_domain,
        r.university_names,
        r.hit_count,
        r.error_count,
        r.course_list_found,
        r.recommended_depth,
        r.root_seed_urls,
        r.detailed_seed_urls,
        r.created_at,
        lower(regexp_replace(coalesce(r.source_domain, ''), '^www\\.', '')) AS source_domain_norm
    FROM seed_observe_results r
),
expanded AS (
    SELECT
        b.*,
        u.url AS candidate_url,
        lower(split_part(regexp_replace(coalesce(u.url, ''), '^[a-zA-Z]+://', ''), '/', 1)) AS candidate_domain,
        lower(regexp_replace(split_part(regexp_replace(coalesce(u.url, ''), '^[a-zA-Z]+://', ''), '/', 1), '^www\\.', '')) AS candidate_domain_norm
    FROM base b
    CROSS JOIN LATERAL (
        SELECT jsonb_array_elements_text(coalesce(b.root_seed_urls, '[]'::jsonb)) AS url
        UNION
        SELECT jsonb_array_elements_text(coalesce(b.detailed_seed_urls, '[]'::jsonb)) AS url
        UNION
        SELECT b.source_url AS url
    ) u
),
flags AS (
    SELECT
        *,
        (
            candidate_domain = 'wikipedia.org' OR candidate_domain LIKE '%.wikipedia.org' OR
            candidate_domain = 'facebook.com' OR candidate_domain LIKE '%.facebook.com' OR
            candidate_domain = 'x.com' OR candidate_domain LIKE '%.x.com' OR
            candidate_domain = 'twitter.com' OR candidate_domain LIKE '%.twitter.com' OR
            candidate_domain = 'instagram.com' OR candidate_domain LIKE '%.instagram.com' OR
            candidate_domain = 'linkedin.com' OR candidate_domain LIKE '%.linkedin.com' OR
            candidate_domain = 'youtube.com' OR candidate_domain LIKE '%.youtube.com' OR
            candidate_domain = 'zoominfo.com' OR candidate_domain LIKE '%.zoominfo.com' OR
            candidate_domain = 'tiktok.com' OR candidate_domain LIKE '%.tiktok.com'
        ) AS f_blocked_domain,
        (
            candidate_domain LIKE '%traininginstitute%' OR
            candidate_domain LIKE '%.docebosaas.com' OR
            candidate_domain LIKE '%coursehero.%' OR
            candidate_domain LIKE '%edurank.%'
        ) AS f_known_noise_pattern,
        (
            candidate_url ILIKE '%/wiki/%' OR
            candidate_url ILIKE '%/profile/%' OR
            candidate_url ILIKE '%/company/%' OR
            candidate_url ILIKE '%/people/%' OR
            candidate_url ILIKE '%/posts/%'
        ) AS f_profile_or_directory_path,
        (
            candidate_domain !~ '(\\.edu$|\\.ac\\.uk$|\\.ac\\.jp$|\\.edu\\.au$|\\.ac\\.nz$|\\.ac\\.ie$|\\.gc\\.ca$|\\.go\\.jp$|\\.gov$|\\.gov\\.uk$)'
        ) AS f_non_academic_tld,
        (
            source_domain_norm <> ''
            AND candidate_domain_norm <> ''
            AND candidate_domain_norm <> source_domain_norm
            AND candidate_domain_norm NOT LIKE ('%.' || source_domain_norm)
            AND source_domain_norm NOT LIKE ('%.' || candidate_domain_norm)
        ) AS f_cross_domain
    FROM expanded
),
scored AS (
    SELECT
        *,
        (
            CASE WHEN f_blocked_domain THEN 5 ELSE 0 END +
            CASE WHEN f_known_noise_pattern THEN 4 ELSE 0 END +
            CASE WHEN f_profile_or_directory_path THEN 2 ELSE 0 END +
            CASE WHEN f_cross_domain THEN 2 ELSE 0 END +
            CASE WHEN f_non_academic_tld THEN 1 ELSE 0 END
        ) AS noise_score,
        concat_ws('; ',
            CASE WHEN f_blocked_domain THEN 'blocked_domain' END,
            CASE WHEN f_known_noise_pattern THEN 'known_noise_pattern' END,
            CASE WHEN f_profile_or_directory_path THEN 'profile_or_directory_path' END,
            CASE WHEN f_cross_domain THEN 'cross_domain' END,
            CASE WHEN f_non_academic_tld THEN 'non_academic_tld' END
        ) AS noise_reasons
    FROM flags
)
SELECT
    run_id,
    external_run_id,
    result_id,
    source_stage,
    source_domain,
    source_url,
    candidate_domain,
    candidate_url,
    course_list_found,
    recommended_depth,
    hit_count,
    error_count,
    noise_score,
    noise_reasons,
    created_at
FROM scored
WHERE noise_score >= 2
ORDER BY noise_score DESC, created_at DESC, result_id DESC;

-- =====================================================================
-- 2) 理由別サマリー
-- =====================================================================
WITH base AS (
    SELECT
        r.id AS result_id,
        r.run_id,
        r.external_run_id,
        r.source_url,
        r.source_domain,
        r.root_seed_urls,
        r.detailed_seed_urls,
        r.created_at,
        lower(regexp_replace(coalesce(r.source_domain, ''), '^www\\.', '')) AS source_domain_norm
    FROM seed_observe_results r
),
expanded AS (
    SELECT
        b.*,
        u.url AS candidate_url,
        lower(split_part(regexp_replace(coalesce(u.url, ''), '^[a-zA-Z]+://', ''), '/', 1)) AS candidate_domain,
        lower(regexp_replace(split_part(regexp_replace(coalesce(u.url, ''), '^[a-zA-Z]+://', ''), '/', 1), '^www\\.', '')) AS candidate_domain_norm
    FROM base b
    CROSS JOIN LATERAL (
        SELECT jsonb_array_elements_text(coalesce(b.root_seed_urls, '[]'::jsonb)) AS url
        UNION
        SELECT jsonb_array_elements_text(coalesce(b.detailed_seed_urls, '[]'::jsonb)) AS url
        UNION
        SELECT b.source_url AS url
    ) u
),
flags AS (
    SELECT
        *,
        (
            candidate_domain = 'wikipedia.org' OR candidate_domain LIKE '%.wikipedia.org' OR
            candidate_domain = 'facebook.com' OR candidate_domain LIKE '%.facebook.com' OR
            candidate_domain = 'x.com' OR candidate_domain LIKE '%.x.com' OR
            candidate_domain = 'twitter.com' OR candidate_domain LIKE '%.twitter.com' OR
            candidate_domain = 'instagram.com' OR candidate_domain LIKE '%.instagram.com' OR
            candidate_domain = 'linkedin.com' OR candidate_domain LIKE '%.linkedin.com' OR
            candidate_domain = 'youtube.com' OR candidate_domain LIKE '%.youtube.com' OR
            candidate_domain = 'zoominfo.com' OR candidate_domain LIKE '%.zoominfo.com' OR
            candidate_domain = 'tiktok.com' OR candidate_domain LIKE '%.tiktok.com'
        ) AS f_blocked_domain,
        (
            candidate_domain LIKE '%traininginstitute%' OR
            candidate_domain LIKE '%.docebosaas.com' OR
            candidate_domain LIKE '%coursehero.%' OR
            candidate_domain LIKE '%edurank.%'
        ) AS f_known_noise_pattern,
        (
            candidate_url ILIKE '%/wiki/%' OR
            candidate_url ILIKE '%/profile/%' OR
            candidate_url ILIKE '%/company/%' OR
            candidate_url ILIKE '%/people/%' OR
            candidate_url ILIKE '%/posts/%'
        ) AS f_profile_or_directory_path,
        (
            candidate_domain !~ '(\\.edu$|\\.ac\\.uk$|\\.ac\\.jp$|\\.edu\\.au$|\\.ac\\.nz$|\\.ac\\.ie$|\\.gc\\.ca$|\\.go\\.jp$|\\.gov$|\\.gov\\.uk$)'
        ) AS f_non_academic_tld,
        (
            source_domain_norm <> ''
            AND candidate_domain_norm <> ''
            AND candidate_domain_norm <> source_domain_norm
            AND candidate_domain_norm NOT LIKE ('%.' || source_domain_norm)
            AND source_domain_norm NOT LIKE ('%.' || candidate_domain_norm)
        ) AS f_cross_domain
    FROM expanded
),
scored AS (
    SELECT
        *,
        (
            CASE WHEN f_blocked_domain THEN 5 ELSE 0 END +
            CASE WHEN f_known_noise_pattern THEN 4 ELSE 0 END +
            CASE WHEN f_profile_or_directory_path THEN 2 ELSE 0 END +
            CASE WHEN f_cross_domain THEN 2 ELSE 0 END +
            CASE WHEN f_non_academic_tld THEN 1 ELSE 0 END
        ) AS noise_score,
        concat_ws('; ',
            CASE WHEN f_blocked_domain THEN 'blocked_domain' END,
            CASE WHEN f_known_noise_pattern THEN 'known_noise_pattern' END,
            CASE WHEN f_profile_or_directory_path THEN 'profile_or_directory_path' END,
            CASE WHEN f_cross_domain THEN 'cross_domain' END,
            CASE WHEN f_non_academic_tld THEN 'non_academic_tld' END
        ) AS noise_reasons
    FROM flags
)
SELECT
    run_id,
    noise_reasons,
    count(*) AS candidate_count,
    min(created_at) AS oldest_created_at,
    max(created_at) AS latest_created_at
FROM scored
WHERE noise_score >= 2
GROUP BY run_id, noise_reasons
ORDER BY run_id DESC, candidate_count DESC, noise_reasons;
-------------
--run ごとの検索クエリ集計

SELECT
r.run_id,
count(*) AS result_count,
sum(coalesce(jsonb_array_length(r.search_queries), 0)) AS total_query_count,
count(DISTINCT q.query_text) AS distinct_query_count,
min(r.created_at) AS oldest_created_at,
max(r.created_at) AS latest_created_at
FROM seed_observe_results r
LEFT JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
ON true
GROUP BY r.run_id
ORDER BY r.run_id DESC;

------------- 
--run ごとの query 使用回数

SELECT
r.run_id,
q.query_text,
count(*) AS used_count
FROM seed_observe_results r
CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(r.search_queries, '[]'::jsonb)) AS q(query_text)
GROUP BY r.run_id, q.query_text
ORDER BY r.run_id DESC, used_count DESC, q.query_text;