CREATE SCHEMA IF NOT EXISTS etl;

CREATE TABLE IF NOT EXISTS etl.crawl_resource_samples (
  id BIGSERIAL PRIMARY KEY,
  log_dt TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  sampled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  elapsed_sec NUMERIC NOT NULL,
  process_cpu_percent NUMERIC,
  process_rss_mb NUMERIC,
  system_cpu_percent NUMERIC,
  system_memory_percent NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_crawl_resource_samples_log_dt ON etl.crawl_resource_samples(log_dt);

CREATE INDEX IF NOT EXISTS idx_crawl_resource_samples_worker_sampled_at
  ON etl.crawl_resource_samples(worker_id, sampled_at);