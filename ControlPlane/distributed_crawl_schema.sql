CREATE SCHEMA IF NOT EXISTS etl;

CREATE TABLE IF NOT EXISTS etl.crawl_worker_runs (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES etl.crawl_runs(id) ON DELETE CASCADE,
  worker_id TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed', 'stopped')),
  messages_received BIGINT NOT NULL DEFAULT 0 CHECK(messages_received >= 0),
  urls_completed BIGINT NOT NULL DEFAULT 0 CHECK(urls_completed >= 0),
  urls_failed BIGINT NOT NULL DEFAULT 0 CHECK(urls_failed >= 0),
  records_extracted BIGINT NOT NULL DEFAULT 0 CHECK(records_extracted >= 0),
  notes TEXT,
  UNIQUE(run_id, worker_id)
);

ALTER TABLE etl.crawl_queue_state
  ADD COLUMN IF NOT EXISTS worker_id TEXT;

ALTER TABLE etl.crawl_attempts
  ADD COLUMN IF NOT EXISTS worker_id TEXT;

ALTER TABLE etl.crawl_resource_samples
  ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES etl.crawl_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_crawl_worker_runs_run_id
  ON etl.crawl_worker_runs(run_id);

CREATE INDEX IF NOT EXISTS idx_crawl_worker_runs_worker_id
  ON etl.crawl_worker_runs(worker_id);

CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_run_worker_status
  ON etl.crawl_queue_state(run_id, worker_id, status);

CREATE INDEX IF NOT EXISTS idx_crawl_attempts_worker_id
  ON etl.crawl_attempts(worker_id);

CREATE INDEX IF NOT EXISTS idx_crawl_resource_samples_run_worker_sampled_at
  ON etl.crawl_resource_samples(run_id, worker_id, sampled_at);
