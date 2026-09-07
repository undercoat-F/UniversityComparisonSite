CREATE TABLE IF NOT EXISTS crawl_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed', 'stopped')),
  root_seed_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS crawl_queue_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
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
  queued_at TEXT NOT NULL DEFAULT (datetime('now')),
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id, url),
  FOREIGN KEY(run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_run_status ON crawl_queue_state(run_id, status);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_run_depth ON crawl_queue_state(run_id, depth);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_state_parent_url ON crawl_queue_state(parent_url);

CREATE TABLE IF NOT EXISTS crawl_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  queue_state_id INTEGER NOT NULL,
  attempt_no INTEGER NOT NULL DEFAULT 1,
  fetch_method TEXT NOT NULL,
  ok INTEGER NOT NULL CHECK(ok IN (0, 1)),
  status_code INTEGER,
  error_type TEXT,
  error_message TEXT,
  final_url TEXT,
  response_bytes INTEGER,
  used_fallback INTEGER NOT NULL DEFAULT 0 CHECK(used_fallback IN (0, 1)),
  connection_log TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(queue_state_id) REFERENCES crawl_queue_state(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_attempts_queue_state_id ON crawl_attempts(queue_state_id);
CREATE INDEX IF NOT EXISTS idx_crawl_attempts_ok ON crawl_attempts(ok);

CREATE TABLE IF NOT EXISTS crawl_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  parent_url TEXT NOT NULL,
  child_url TEXT NOT NULL,
  parent_domain TEXT NOT NULL,
  child_domain TEXT NOT NULL,
  depth INTEGER NOT NULL CHECK(depth >= 0),
  source TEXT NOT NULL DEFAULT 'link' CHECK(source IN ('link', 'sitemap', 'seed', 'manual')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id, parent_url, child_url, source),
  FOREIGN KEY(run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_edges_run_id ON crawl_edges(run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_edges_parent_url ON crawl_edges(parent_url);
CREATE INDEX IF NOT EXISTS idx_crawl_edges_child_url ON crawl_edges(child_url);

CREATE TABLE IF NOT EXISTS crawl_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  url TEXT NOT NULL,
  domain TEXT NOT NULL,
  fetch_method TEXT,
  status_code INTEGER,
  error_type TEXT,
  error_message TEXT,
  connection_log TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_failures_run_id ON crawl_failures(run_id);
CREATE INDEX IF NOT EXISTS idx_crawl_failures_domain ON crawl_failures(domain);