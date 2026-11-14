CREATE TABLE owners (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at REAL NOT NULL
);
CREATE TABLE branches (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT,
    request_hash TEXT,
    UNIQUE(owner_id, idempotency_key)
);
CREATE TABLE checkpoints (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE reports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    owner_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    deleted_at REAL
);
CREATE TABLE history (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    deleted_at REAL
);
CREATE TABLE evidence (
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    review_id TEXT NOT NULL,
    text TEXT NOT NULL,
    url TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY(report_id, review_id)
);
CREATE TABLE shares (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX ix_jobs_owner ON jobs(owner_id);
CREATE INDEX ix_history_owner ON history(owner_id, created_at);
CREATE INDEX ix_shares_report ON shares(report_id);
