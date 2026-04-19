-- Initial schema for image-gen-service.
-- Owned by Cycle 1. Matches arch §4.2 column-for-column.
-- Webhook columns (webhook_*) ship nullable now; dispatcher behaviour lands in Cycle 9.

CREATE TABLE IF NOT EXISTS schema_version (
    filename   TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                         TEXT PRIMARY KEY,
    model_name                 TEXT NOT NULL,
    input_json                 TEXT NOT NULL,
    mode                       TEXT NOT NULL CHECK (mode IN ('sync','async')),
    status                     TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','abandoned')),
    result_json                TEXT,
    error_code                 TEXT,
    error_message              TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    client_id                  TEXT,                    -- NULL until ComfyUI submit (set alongside prompt_id in set_running; Cycle 2+)
    prompt_id                  TEXT,
    output_keys                TEXT,
    response_delivered         INTEGER NOT NULL DEFAULT 0 CHECK (response_delivered IN (0,1)),
    initial_response_delivered INTEGER NOT NULL DEFAULT 0 CHECK (initial_response_delivered IN (0,1)),
    webhook_url                TEXT,
    webhook_headers_json       TEXT,
    webhook_delivery_status    TEXT CHECK (webhook_delivery_status IN ('pending','succeeded','failed','suppressed') OR webhook_delivery_status IS NULL),
    webhook_handover           INTEGER NOT NULL DEFAULT 0 CHECK (webhook_handover IN (0,1))
);

-- Index rationale (Cycle 4 readers):
--   idx_jobs_status_updated — orphan reaper scan (status='completed' AND updated_at < cutoff)
--                             and boot scan (status IN ('queued','running') order by updated_at).
--   idx_jobs_created_at     — TTL prune (created_at < now - JOB_RECORD_TTL).
CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at     ON jobs(created_at);
