-- Cycle 4: track when a completed job's image was first fetched via the gateway.
-- Orphan reaper (app/queue/reaper.py) targets completed-but-never-fetched rows.

ALTER TABLE jobs ADD COLUMN fetched_at TEXT;

-- Composite index for the reaper scan:
--   WHERE status = 'completed' AND fetched_at IS NULL AND updated_at < cutoff
CREATE INDEX IF NOT EXISTS idx_jobs_completed_unfetched
    ON jobs(status, fetched_at, updated_at);
