CREATE SCHEMA IF NOT EXISTS etl;

CREATE TABLE IF NOT EXISTS ${CRAWL_RUNS_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed', 'stopped', 'timed_out', 'stalled')),
  root_seed_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

DO $$
DECLARE
  c RECORD;
BEGIN
  FOR c IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = '${CRAWL_RUNS_TABLE}'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%status%'
  LOOP
    EXECUTE format('ALTER TABLE ${CRAWL_RUNS_TABLE} DROP CONSTRAINT %I', c.conname);
  END LOOP;

  EXECUTE 'ALTER TABLE ${CRAWL_RUNS_TABLE} ADD CONSTRAINT crawl_runs_status_check '
          || 'CHECK(status IN (''running'', ''completed'', ''failed'', ''stopped'', ''timed_out'', ''stalled''))';
EXCEPTION
  WHEN duplicate_object THEN
    NULL;
END
$$;

CREATE TABLE IF NOT EXISTS ${CRAWL_QUEUE_STATE_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  parent_url TEXT,
  domain TEXT NOT NULL,
  depth INTEGER NOT NULL CHECK(depth >= 0),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'done', 'error', 'skipped')),
  fetch_method TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  discovered_from TEXT,
  last_error_type TEXT,
  last_error_message TEXT,
  status_code INTEGER,
  queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_run_status ON ${CRAWL_QUEUE_STATE_TABLE}(run_id, status);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_run_depth ON ${CRAWL_QUEUE_STATE_TABLE}(run_id, depth);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_parent_url ON ${CRAWL_QUEUE_STATE_TABLE}(parent_url);

CREATE TABLE IF NOT EXISTS ${CRAWL_ATTEMPTS_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  queue_state_id BIGINT NOT NULL REFERENCES crawl_queue_state(id) ON DELETE CASCADE,
  attempt_no INTEGER NOT NULL DEFAULT 1,
  fetch_method TEXT NOT NULL,
  ok BOOLEAN NOT NULL,
  status_code INTEGER,
  error_type TEXT,
  error_message TEXT,
  final_url TEXT,
  response_bytes INTEGER,
  used_fallback BOOLEAN NOT NULL DEFAULT FALSE,
  connection_log JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_attempts_queue_state_id ON ${CRAWL_ATTEMPTS_TABLE}(queue_state_id);
CREATE INDEX IF NOT EXISTS idx_crawl_attempts_ok ON ${CRAWL_ATTEMPTS_TABLE}(ok);

CREATE TABLE IF NOT EXISTS ${CRAWL_EDGES_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  parent_url TEXT NOT NULL,
  child_url TEXT NOT NULL,
  parent_domain TEXT NOT NULL,
  child_domain TEXT NOT NULL,
  depth INTEGER NOT NULL CHECK(depth >= 0),
  source TEXT NOT NULL DEFAULT 'link' CHECK(source IN ('link', 'sitemap', 'seed', 'manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(run_id, parent_url, child_url, source)
);

CREATE INDEX IF NOT EXISTS idx_crawl_edges_run_id ON ${CRAWL_EDGES_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_edges_parent_url ON ${CRAWL_EDGES_TABLE}(parent_url);
CREATE INDEX IF NOT EXISTS idx_crawl_edges_child_url ON ${CRAWL_EDGES_TABLE}(child_url);

CREATE TABLE IF NOT EXISTS ${CRAWL_FAILURES_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  domain TEXT NOT NULL,
  fetch_method TEXT,
  status_code INTEGER,
  error_type TEXT,
  error_message TEXT,
  connection_log JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_failures_run_id ON ${CRAWL_FAILURES_TABLE}(run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_failures_domain ON ${CRAWL_FAILURES_TABLE}(domain);

CREATE TABLE IF NOT EXISTS ${CRAWL_TAG_KEYWORD_HITS_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  domain TEXT NOT NULL,
  url TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  course_type TEXT NOT NULL,
  keyword TEXT NOT NULL,
  hit_count INTEGER NOT NULL CHECK(hit_count >= 0),
  weight INTEGER NOT NULL CHECK(weight >= 0),
  weighted_score INTEGER NOT NULL CHECK(weighted_score >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_tag_keyword_hits_run_domain
  ON ${CRAWL_TAG_KEYWORD_HITS_TABLE}(run_id, domain);
CREATE INDEX IF NOT EXISTS idx_crawl_tag_keyword_hits_run_url
  ON ${CRAWL_TAG_KEYWORD_HITS_TABLE}(run_id, url);
CREATE INDEX IF NOT EXISTS idx_crawl_tag_keyword_hits_tag
  ON ${CRAWL_TAG_KEYWORD_HITS_TABLE}(tag_name, keyword);

CREATE TABLE IF NOT EXISTS ${CRAWL_DOMAIN_TAG_SCORES_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  domain TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  course_type TEXT NOT NULL,
  keyword TEXT NOT NULL,
  total_hits BIGINT NOT NULL DEFAULT 0,
  total_weighted_score BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(run_id, domain, tag_name, course_type, keyword)
);

CREATE INDEX IF NOT EXISTS idx_crawl_domain_tag_scores_run_domain
  ON ${CRAWL_DOMAIN_TAG_SCORES_TABLE}(run_id, domain);

CREATE TABLE IF NOT EXISTS ${CRAWL_TAG_CLASS_COUNTS_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  domain TEXT NOT NULL,
  url TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  class_name TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL CHECK(occurrence_count >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_tag_class_counts_run_domain
  ON ${CRAWL_TAG_CLASS_COUNTS_TABLE}(run_id, domain);
CREATE INDEX IF NOT EXISTS idx_crawl_tag_class_counts_run_url
  ON ${CRAWL_TAG_CLASS_COUNTS_TABLE}(run_id, url);
CREATE INDEX IF NOT EXISTS idx_crawl_tag_class_counts_tag_class
  ON ${CRAWL_TAG_CLASS_COUNTS_TABLE}(tag_name, class_name);

CREATE TABLE IF NOT EXISTS ${CRAWL_DOMAIN_CLASS_COUNTS_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
  domain TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  class_name TEXT NOT NULL,
  total_occurrences BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(run_id, domain, tag_name, class_name)
);

CREATE INDEX IF NOT EXISTS idx_crawl_domain_class_counts_run_domain
  ON ${CRAWL_DOMAIN_CLASS_COUNTS_TABLE}(run_id, domain);
