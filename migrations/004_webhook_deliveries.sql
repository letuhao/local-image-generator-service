CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    attempt_n INTEGER NOT NULL CHECK (attempt_n BETWEEN 1 AND 5),
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivering', 'succeeded', 'failed')),
    status_code INTEGER,
    response_body_snippet TEXT,
    error TEXT,
    error_code TEXT,
    next_retry_at TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_job_id ON webhook_deliveries(job_id);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries(status, next_retry_at);
