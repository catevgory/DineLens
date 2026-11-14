CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    report_id TEXT NOT NULL UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    read_at REAL
);
CREATE INDEX ix_notifications_owner ON notifications(owner_id, created_at);
