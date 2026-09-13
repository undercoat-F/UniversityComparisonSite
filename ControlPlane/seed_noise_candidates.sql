-- seed_urls からノイズ候補を抽出するための SQL（削除はしない）
-- 目的:
-- 1) ノイズ疑いのレコードを理由つきで一覧化
-- 2) 理由別の件数を確認
--
-- 想定DB: PostgreSQL (Neon)

-- =====================================================================
-- 1) ノイズ候補レコード一覧
-- =====================================================================
WITH base AS (
    SELECT
        id,
        country,
        domain,
        root_url,
        depth,
        enabled,
        created_at,
        updated_at,
        lower(coalesce(domain, '')) AS domain_l,
        lower(coalesce(root_url, '')) AS root_url_l
    FROM seed_urls
),
flags AS (
    SELECT
        *,
        (
            domain_l = 'wikipedia.org' OR domain_l LIKE '%.wikipedia.org' OR
            domain_l = 'facebook.com' OR domain_l LIKE '%.facebook.com' OR
            domain_l = 'x.com' OR domain_l LIKE '%.x.com' OR
            domain_l = 'twitter.com' OR domain_l LIKE '%.twitter.com' OR
            domain_l = 'instagram.com' OR domain_l LIKE '%.instagram.com' OR
            domain_l = 'linkedin.com' OR domain_l LIKE '%.linkedin.com' OR
            domain_l = 'youtube.com' OR domain_l LIKE '%.youtube.com' OR
            domain_l = 'zoominfo.com' OR domain_l LIKE '%.zoominfo.com' OR
            domain_l = 'tiktok.com' OR domain_l LIKE '%.tiktok.com'
        ) AS f_blocked_domain,
        (country = 'unknown') AS f_unknown_country,
        (
            domain_l !~ '(\\.edu$|\\.ac\\.uk$|\\.ac\\.jp$|\\.edu\\.au$|\\.ac\\.nz$|\\.ac\\.ie$|\\.gc\\.ca$|\\.go\\.jp$|\\.gov$|\\.gov\\.uk$)'
        ) AS f_non_academic_tld,
        (
            root_url_l LIKE '%/wiki/%' OR
            root_url_l LIKE '%/profile/%' OR
            root_url_l LIKE '%/company/%' OR
            root_url_l LIKE '%/people/%' OR
            root_url_l LIKE '%/posts/%'
        ) AS f_profile_or_directory_path,
        (
            domain_l LIKE '%traininginstitute%' OR
            domain_l LIKE '%.docebosaas.com' OR
            domain_l LIKE '%coursehero.%' OR
            domain_l LIKE '%edurank.%'
        ) AS f_known_noise_pattern
    FROM base
),
scored AS (
    SELECT
        *,
        (
            CASE WHEN f_blocked_domain THEN 5 ELSE 0 END +
            CASE WHEN f_known_noise_pattern THEN 4 ELSE 0 END +
            CASE WHEN f_profile_or_directory_path THEN 2 ELSE 0 END +
            CASE WHEN f_unknown_country THEN 1 ELSE 0 END +
            CASE WHEN f_non_academic_tld THEN 1 ELSE 0 END
        ) AS noise_score,
        concat_ws('; ',
            CASE WHEN f_blocked_domain THEN 'blocked_domain' END,
            CASE WHEN f_known_noise_pattern THEN 'known_noise_pattern' END,
            CASE WHEN f_profile_or_directory_path THEN 'profile_or_directory_path' END,
            CASE WHEN f_unknown_country THEN 'unknown_country' END,
            CASE WHEN f_non_academic_tld THEN 'non_academic_tld' END
        ) AS noise_reasons
    FROM flags
)
SELECT
    id,
    country,
    domain,
    root_url,
    depth,
    enabled,
    noise_score,
    noise_reasons,
    created_at,
    updated_at
FROM scored
WHERE noise_score >= 2
ORDER BY noise_score DESC, updated_at DESC, id DESC;

-- =====================================================================
-- 2) 理由別の件数サマリー（上記とセットで使用）
-- =====================================================================
WITH base AS (
    SELECT
        id,
        country,
        domain,
        root_url,
        depth,
        enabled,
        created_at,
        updated_at,
        lower(coalesce(domain, '')) AS domain_l,
        lower(coalesce(root_url, '')) AS root_url_l
    FROM seed_urls
),
flags AS (
    SELECT
        *,
        (
            domain_l = 'wikipedia.org' OR domain_l LIKE '%.wikipedia.org' OR
            domain_l = 'facebook.com' OR domain_l LIKE '%.facebook.com' OR
            domain_l = 'x.com' OR domain_l LIKE '%.x.com' OR
            domain_l = 'twitter.com' OR domain_l LIKE '%.twitter.com' OR
            domain_l = 'instagram.com' OR domain_l LIKE '%.instagram.com' OR
            domain_l = 'linkedin.com' OR domain_l LIKE '%.linkedin.com' OR
            domain_l = 'youtube.com' OR domain_l LIKE '%.youtube.com' OR
            domain_l = 'zoominfo.com' OR domain_l LIKE '%.zoominfo.com' OR
            domain_l = 'tiktok.com' OR domain_l LIKE '%.tiktok.com'
        ) AS f_blocked_domain,
        (country = 'unknown') AS f_unknown_country,
        (
            domain_l !~ '(\\.edu$|\\.ac\\.uk$|\\.ac\\.jp$|\\.edu\\.au$|\\.ac\\.nz$|\\.ac\\.ie$|\\.gc\\.ca$|\\.go\\.jp$|\\.gov$|\\.gov\\.uk$)'
        ) AS f_non_academic_tld,
        (
            root_url_l LIKE '%/wiki/%' OR
            root_url_l LIKE '%/profile/%' OR
            root_url_l LIKE '%/company/%' OR
            root_url_l LIKE '%/people/%' OR
            root_url_l LIKE '%/posts/%'
        ) AS f_profile_or_directory_path,
        (
            domain_l LIKE '%traininginstitute%' OR
            domain_l LIKE '%.docebosaas.com' OR
            domain_l LIKE '%coursehero.%' OR
            domain_l LIKE '%edurank.%'
        ) AS f_known_noise_pattern
    FROM base
),
scored AS (
    SELECT
        *,
        (
            CASE WHEN f_blocked_domain THEN 5 ELSE 0 END +
            CASE WHEN f_known_noise_pattern THEN 4 ELSE 0 END +
            CASE WHEN f_profile_or_directory_path THEN 2 ELSE 0 END +
            CASE WHEN f_unknown_country THEN 1 ELSE 0 END +
            CASE WHEN f_non_academic_tld THEN 1 ELSE 0 END
        ) AS noise_score,
        concat_ws('; ',
            CASE WHEN f_blocked_domain THEN 'blocked_domain' END,
            CASE WHEN f_known_noise_pattern THEN 'known_noise_pattern' END,
            CASE WHEN f_profile_or_directory_path THEN 'profile_or_directory_path' END,
            CASE WHEN f_unknown_country THEN 'unknown_country' END,
            CASE WHEN f_non_academic_tld THEN 'non_academic_tld' END
        ) AS noise_reasons
    FROM flags
)
SELECT
    noise_reasons,
    count(*) AS candidate_count,
    min(updated_at) AS oldest_updated_at,
    max(updated_at) AS latest_updated_at
FROM scored
WHERE noise_score >= 2
GROUP BY noise_reasons
ORDER BY candidate_count DESC, noise_reasons;
