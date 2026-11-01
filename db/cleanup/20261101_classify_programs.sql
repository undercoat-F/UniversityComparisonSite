-- 学位プログラムではない行に除外の理由を付ける（論理削除）。2026-11-01
-- 判定の優先順: not_university > not_program > listing_page > short_course > needs_review
-- needs_review は確認待ちの印で、検索結果からは除外しない（抽出プログラム改善の材料）
-- バックアップ: backup_20261101.degree_programs

-- NULL は通常の行。値があれば理由を表す（needs_review 以外は検索結果に出さない）
ALTER TABLE public.degree_programs ADD COLUMN IF NOT EXISTS excluded_reason VARCHAR(50);

WITH rules AS (
    SELECT
        p.id,
        CASE
            WHEN u.url ~* '://(www\.)?(gakurekirank\.com|collegestudysmarts\.com|britishuniversity\.com|internationalstudent\.com|vuedutech\.com|schools\.nyc\.gov|powhatan\.k12\.va\.us|alis\.alberta\.ca|cilaschool\.org)'
                THEN 'not_university'
            WHEN p.program_name ~* '(open day|faq|frequently asked|how to |guide to|your guide|careers? (after|change)|what are|events?\M|webinar|news|blog|scholarship|apply now|accommodation|portal|student life|why study|study with us|contact us)'
                THEN 'not_program'
            WHEN p.program_name ~* '^\s*(all |our |list of |find a |browse |search )?(undergraduate|postgraduate|graduate|taught|research|online|short)?\s*(taught |research )?(courses|programmes|programs|degrees|qualifications|study|study options|courses and programmes)( and (courses|qualifications|degrees))?\s*($|\|)'
                THEN 'listing_page'
            WHEN p.program_name ~* '(short course|micro-?credential|professional development|\mcpd\M|online course \|)'
                THEN 'short_course'
            WHEN p.program_name !~* '(bachelor|master|doctor|doctorate|ph\.?d|diploma|certificate|degree|associate|honours|foundation year|postgraduate|undergraduate|pgce|pgcert|pgdip|gradcert|graddip|ugdip|ugcert|certhe|diphe)'
             AND p.program_name !~ '\m(B|M|D)[A-Z][A-Za-z]{0,6}\M|\((B|M|D)[A-Za-z]{1,6}\)'
                THEN 'needs_review'
        END AS reason
    FROM public.degree_programs p
    JOIN public.universities u ON u.id = p.university_id
)
UPDATE public.degree_programs p
SET excluded_reason = r.reason
FROM rules r
WHERE r.id = p.id
  AND r.reason IS NOT NULL;
