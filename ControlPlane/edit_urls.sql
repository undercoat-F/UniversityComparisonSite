/*
URLリスト編集用SQL
追加
INSERT INTO ${SEED_URLS_TABLE} (country, domain, root_url, depth, enabled) VALUES (...);

更新
UPDATE ${SEED_URLS_TABLE} SET depth=..., enabled=... WHERE domain=... AND root_url=...;

無効化
UPDATE ${SEED_URLS_TABLE} SET enabled=0 WHERE ...;

確認
SELECT id, domain, root_url, depth, enabled FROM ${SEED_URLS_TABLE} ORDER BY id
*/


-- 集計用　直近14日サマリー（件数、平均、分位、最大）
WITH recent AS (
SELECT error_count
FROM ${SEED_OBSERVE_RESULTS_TABLE}
WHERE created_at >= now() - interval '14 days'
)
SELECT
count(*) AS n,
avg(error_count)::numeric(10,2) AS avg_err,
percentile_cont(0.5) WITHIN GROUP (ORDER BY error_count) AS p50,
percentile_cont(0.75) WITHIN GROUP (ORDER BY error_count) AS p75,
percentile_cont(0.9) WITHIN GROUP (ORDER BY error_count) AS p90,
max(error_count) AS max_err
FROM recent;

-- 集計用　直近14日ヒストグラム
SELECT
error_count,
count(*) AS row_count
FROM ${SEED_OBSERVE_RESULTS_TABLE}
WHERE created_at >= now() - interval '14 days'
GROUP BY error_count
ORDER BY error_count;
-----------------------
-- 1) crawl_runs に集計カラム追加
ALTER TABLE ${CRAWL_RUNS_TABLE}
ADD COLUMN IF NOT EXISTS total_course_count BIGINT NOT NULL DEFAULT 0;

-- 2) 既存データを backfill
-- run_id ごとに、同runで完了したURL(source_url)に紐づく degree_programs を数える
UPDATE crawl_runs r
SET total_course_count = s.course_count
FROM (
    SELECT
        qs.run_id,
        COUNT(DISTINCT dp.id)::bigint AS course_count
    FROM ${CRAWL_QUEUE_STATE_TABLE} qs
    JOIN ${DEGREE_PROGRAMS_TABLE} dp
      ON dp.source_url = qs.url
    WHERE qs.status = 'done'
    GROUP BY qs.run_id
) s
WHERE r.id = s.run_id;
-----------------------
--現在の集計用
SELECT COUNT(*) AS total_course_count
FROM ${DEGREE_PROGRAMS_TABLE};

--授業料情報との紐付き状況も同時に見たい場合
SELECT
  COUNT(DISTINCT dp.id) AS total_courses,
  COUNT(DISTINCT CASE WHEN ptm.degree_program_id IS NOT NULL THEN dp.id END) AS courses_with_tuition,
  COUNT(DISTINCT CASE WHEN ptm.degree_program_id IS NULL THEN dp.id END) AS courses_without_tuition,
  COUNT(DISTINCT u.id) AS university_count,
  COUNT(DISTINCT tp.id) AS tuition_pattern_count
FROM ${DEGREE_PROGRAMS_TABLE} dp
JOIN ${UNIVERSITIES_TABLE} u
  ON u.id = dp.university_id
LEFT JOIN ${PROGRAM_TUITION_MAP_TABLE} ptm
  ON ptm.degree_program_id = dp.id
LEFT JOIN ${TUITION_PATTERNS_TABLE} tp
  ON tp.id = ptm.tuition_pattern_id;

--大学別件数
SELECT
  u.id AS university_id,
  u.name AS university_name,
  COUNT(DISTINCT dp.id) AS total_courses,
  COUNT(DISTINCT CASE WHEN ptm.degree_program_id IS NOT NULL THEN dp.id END) AS courses_with_tuition
FROM ${UNIVERSITIES_TABLE} u
LEFT JOIN ${DEGREE_PROGRAMS_TABLE} dp
  ON dp.university_id = u.id
LEFT JOIN ${PROGRAM_TUITION_MAP_TABLE} ptm
  ON ptm.degree_program_id = dp.id
GROUP BY u.id, u.name
ORDER BY total_courses DESC, u.name;

-----
-- 特定runで、worker別の処理結果を集計
SELECT worker_id, status, COUNT(*)
FROM etl.crawl_queue_state
WHERE run_id = 123
GROUP BY worker_id, status;

-- worker別のCPU/RSS時系列を見る
SELECT sampled_at, process_cpu_percent, process_rss_mb
FROM etl.crawl_resource_samples
WHERE run_id = 123 AND worker_id = 'worker-identifier'
ORDER BY sampled_at;