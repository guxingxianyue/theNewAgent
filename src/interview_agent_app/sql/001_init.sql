CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    operator TEXT NOT NULL,
    interview_type TEXT NOT NULL,
    target TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    model TEXT NOT NULL,
    topic TEXT,
    mode TEXT,
    retry_source_session_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    round_index INTEGER,
    score REAL,
    tags TEXT,
    latency_ms INTEGER,
    model TEXT,
    token_usage TEXT,
    metadata_json TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator TEXT NOT NULL,
    interview_type TEXT NOT NULL,
    target TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_memories_lookup
    ON memories(operator, interview_type, target, created_at);

CREATE INDEX IF NOT EXISTS idx_events_session
    ON events(session_id, created_at);
