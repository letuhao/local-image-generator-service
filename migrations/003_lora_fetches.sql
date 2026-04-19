-- Cycle 6: lora_fetches table.
-- Tracks async Civitai-fetch requests: queued → downloading → verifying → done|failed.
-- Separate from `jobs` (different domain, lifecycle, columns) per spec §8.2.

CREATE TABLE IF NOT EXISTS lora_fetches (
    id                   TEXT PRIMARY KEY,                -- ksuid
    url                  TEXT NOT NULL,                   -- original URL from admin
    civitai_model_id     INTEGER,                         -- NULL on /api/download/models/<vid> URLs
    civitai_version_id   INTEGER NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('pending','downloading','verifying','done','failed')),
    progress_bytes       INTEGER NOT NULL DEFAULT 0,
    total_bytes          INTEGER,                         -- NULL until metadata fetched
    dest_name            TEXT,                            -- canonical 'civitai/<slug>_<version_id>' once done
    error_code           TEXT,
    error_message        TEXT,
    handover             INTEGER NOT NULL DEFAULT 0 CHECK (handover IN (0,1)),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Dedupe at DB layer: only ONE non-terminal row per version_id allowed.
-- Two concurrent POSTs with the same URL both pass find_active_by_version()=None,
-- then race to INSERT. This unique partial index catches the race; the loser
-- catches IntegrityError and re-queries, returning the winner's request_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_lora_fetches_active_version
    ON lora_fetches(civitai_version_id)
    WHERE status IN ('pending','downloading','verifying');

-- Status+updated_at scan for recovery (boot) and future TTL prune.
CREATE INDEX IF NOT EXISTS idx_lora_fetches_status_updated
    ON lora_fetches(status, updated_at);
