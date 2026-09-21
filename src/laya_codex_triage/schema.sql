PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    capture_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    repository_fingerprint TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    turn_hash TEXT NOT NULL,
    active_model TEXT NOT NULL,
    permission_mode TEXT NOT NULL,
    prompt TEXT,
    prompt_truncated INTEGER NOT NULL,
    redaction_count INTEGER NOT NULL,
    redaction_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'completed', 'failed', 'quarantined')),
    failure_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    last_error_code TEXT
);

CREATE INDEX IF NOT EXISTS captures_queue_idx
    ON captures(status, captured_at, capture_id);

CREATE TABLE IF NOT EXISTS predictions (
    capture_id TEXT PRIMARY KEY REFERENCES captures(capture_id) ON DELETE CASCADE,
    predicted_at TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    checkpoint_revision TEXT NOT NULL,
    decision_schema_version TEXT NOT NULL,
    calibration_version TEXT,
    model_tier TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    model_tier_distribution TEXT NOT NULL,
    reasoning_effort_distribution TEXT NOT NULL,
    abstained INTEGER NOT NULL,
    abstention_reason TEXT,
    queue_delay_ms REAL NOT NULL,
    inference_latency_ms REAL NOT NULL,
    prediction_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    capture_id TEXT PRIMARY KEY REFERENCES captures(capture_id) ON DELETE CASCADE,
    model_tier TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    note TEXT,
    reviewed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibrations (
    calibration_version TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source_window TEXT NOT NULL,
    method TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    activated_at TEXT
);

CREATE TABLE IF NOT EXISTS route_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT REFERENCES captures(capture_id) ON DELETE SET NULL,
    decided_at TEXT NOT NULL,
    operating_mode TEXT NOT NULL,
    suggested_tier TEXT NOT NULL,
    suggested_effort TEXT NOT NULL,
    policy_tier TEXT NOT NULL,
    policy_effort TEXT NOT NULL,
    model_slug TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    applied_rule TEXT NOT NULL,
    user_override_json TEXT,
    launch_outcome TEXT,
    decision_json TEXT NOT NULL
);

INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO metadata(key, value) VALUES ('dropped_captures', '0');

PRAGMA user_version = 1;

