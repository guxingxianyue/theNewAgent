-- Compatibility migration manifest.
--
-- SQLite versions commonly bundled with Python may not support
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so repository.py reads this
-- manifest and applies each ADD COLUMN statement while ignoring duplicate
-- column errors.

ALTER TABLE sessions ADD COLUMN topic TEXT;
ALTER TABLE sessions ADD COLUMN mode TEXT;
ALTER TABLE sessions ADD COLUMN retry_source_session_id TEXT;

ALTER TABLE events ADD COLUMN round_index INTEGER;
ALTER TABLE events ADD COLUMN score REAL;
ALTER TABLE events ADD COLUMN tags TEXT;
ALTER TABLE events ADD COLUMN latency_ms INTEGER;
ALTER TABLE events ADD COLUMN model TEXT;
ALTER TABLE events ADD COLUMN token_usage TEXT;
ALTER TABLE events ADD COLUMN metadata_json TEXT;
