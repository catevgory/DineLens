"""Versioned SQLite storage for private jobs, reports and share capabilities."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class Storage:
    def __init__(self, path, *, share_days=30, raw_days=90, report_days=365, clock=time.time):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.share_days, self.raw_days, self.report_days = share_days, raw_days, report_days
        if min(share_days, raw_days, report_days) <= 0:
            raise ValueError("Retention periods must be positive")
        self.clock = clock
        self.migrate()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()
        with self.connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY)")
            version = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_versions").fetchone()[0]
            if version > 2:
                raise RuntimeError("Database schema is newer than this application")
            migrations = ((1, "001_initial.sql"), (2, "002_notifications.sql"))
            for target, filename in migrations:
                if target <= version:
                    continue
                sql = (Path(__file__).parent / "migrations" / filename).read_text(encoding="utf-8")
                for statement in sql.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute("INSERT INTO schema_versions VALUES (?)", (target,))

    def create_owner(self):
        token = secrets.token_urlsafe(32)
        owner_id = secrets.token_hex(16)
        expiry = self.clock() + 365 * 86400
        with self.connection() as conn:
            conn.execute("INSERT INTO owners VALUES (?,?,?)", (owner_id, token_hash(token), expiry))
        return token, owner_id, iso(expiry)

    def owner(self, token):
        if not token:
            return None
        with self.connection() as conn:
            row = conn.execute("SELECT id, expires_at FROM owners WHERE token_hash=? AND expires_at>?", (token_hash(token), self.clock())).fetchone()
            return dict(row) if row else None

    def remember_branch(self, branch):
        key = token_hash(encode([branch["source"], branch["id"], branch["city"], branch.get("address")]))[:32]
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO branches VALUES (?,?)", (key, encode(branch)))
        return {**branch, "branch_id": key}

    def branch(self, key):
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM branches WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_job(self, job, *, key=None, request_hash=None):
        with self.connection() as conn:
            conn.execute("INSERT INTO jobs(id,owner_id,payload,idempotency_key,request_hash) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (job["id"], job.get("owner_id", ""), encode(job), key, request_hash))

    def existing_job(self, owner_id, key, request_hash):
        if not key:
            return None
        with self.connection() as conn:
            row = conn.execute("SELECT id,request_hash FROM jobs WHERE owner_id=? AND idempotency_key=?", (owner_id, key)).fetchone()
            if row and row["request_hash"] != request_hash:
                raise ValueError("Idempotency key was used with a different request")
            return row["id"] if row else None

    def load_jobs(self):
        with self.connection() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]

    def checkpoint(self, job_id, rows=None):
        with self.connection() as conn:
            if rows is not None:
                conn.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)", (job_id, encode(rows), self.clock() + self.raw_days * 86400))
                return rows
            row = conn.execute("SELECT payload FROM checkpoints WHERE job_id=? AND expires_at>?", (job_id, self.clock())).fetchone()
            return json.loads(row[0]) if row else None

    def complete(self, job, report):
        report_id = "report_" + job["id"]
        data = json.loads(encode(report))
        raw = data.pop("evidence", {})
        for quote in data["summary"]:
            evidence = raw.get(quote["review_id"], {})
            if evidence.get("text", "")[quote["start"]:quote["end"]] != quote["quote"]:
                raise ValueError("Report quote has no matching evidence")
            quote["quote"] = None
        now = self.clock()
        data.update(report_id=report_id, generated_at=iso(now))
        with self.connection() as conn:
            existing = conn.execute("SELECT id FROM reports WHERE job_id=?", (job["id"],)).fetchone()
            if existing:
                return existing[0]
            conn.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,NULL)",
                         (report_id, job["id"], job.get("owner_id", ""), encode(data), now, now + self.report_days * 86400))
            conn.execute("INSERT INTO history VALUES (?,?,?,?,NULL)", ("history_" + job["id"], job.get("owner_id", ""), report_id, now))
            checkpoint = conn.execute("SELECT expires_at FROM checkpoints WHERE job_id=?", (job["id"],)).fetchone()
            evidence_expiry = min(now + self.raw_days * 86400, checkpoint[0]) if checkpoint else now + self.raw_days * 86400
            for rid, value in raw.items():
                conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?)", (report_id, rid, value["text"], value.get("url", ""), evidence_expiry))
            saved = {**job, "report": None, "report_id": report_id}
            conn.execute("UPDATE jobs SET payload=? WHERE id=?", (encode(saved), job["id"]))
            conn.execute("DELETE FROM checkpoints WHERE job_id=?", (job["id"],))
        return report_id

    def _report(self, conn, report_id):
        row = conn.execute("SELECT * FROM reports WHERE id=? AND deleted_at IS NULL AND expires_at>?", (report_id, self.clock())).fetchone()
        if not row:
            return None
        data = json.loads(row["payload"])
        quotes = {r["review_id"]: r for r in conn.execute("SELECT * FROM evidence WHERE report_id=? AND expires_at>?", (report_id, self.clock()))}
        expired = False
        for item in data["summary"]:
            value = quotes.get(item["review_id"])
            if value:
                item["quote"] = value["text"][item["start"]:item["end"]]
                item["url"] = value["url"]
            else:
                item.update(quote=None, start=None, end=None, evidence_status="expired")
                expired = True
        if expired:
            data["warnings"] = list(dict.fromkeys([*data["warnings"], "EVIDENCE_EXPIRED"]))
        return data

    def report(self, report_id, owner_id):
        with self.connection() as conn:
            owned = conn.execute("SELECT 1 FROM reports WHERE id=? AND owner_id=?", (report_id, owner_id)).fetchone()
            return self._report(conn, report_id) if owned else None

    def history(self, owner_id, limit=20, offset=0):
        with self.connection() as conn:
            rows = conn.execute("SELECT h.* FROM history h JOIN reports r ON r.id=h.report_id WHERE h.owner_id=? "
                                "AND h.deleted_at IS NULL AND r.deleted_at IS NULL AND r.expires_at>? "
                                "ORDER BY h.created_at DESC,h.id DESC LIMIT ? OFFSET ?", (owner_id, self.clock(), limit + 1, offset)).fetchall()
            items = []
            for row in rows[:limit]:
                data = self._report(conn, row["report_id"])
                items.append({"history_id": row["id"], "report_id": row["report_id"], "generated_at": iso(row["created_at"]),
                              "branch": data["branch"], "score": data["score"]})
            return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}

    def delete_history(self, history_id, owner_id):
        with self.connection() as conn:
            row = conn.execute("SELECT report_id FROM history WHERE id=? AND owner_id=?", (history_id, owner_id)).fetchone()
            if not row:
                return False
            now = self.clock()
            conn.execute("UPDATE history SET deleted_at=? WHERE id=?", (now, history_id))
            conn.execute("UPDATE reports SET deleted_at=? WHERE id=?", (now, row[0]))
            conn.execute("UPDATE shares SET revoked_at=? WHERE report_id=?", (now, row[0]))
            conn.execute("DELETE FROM evidence WHERE report_id=?", (row[0],))
            conn.execute("DELETE FROM notifications WHERE report_id=?", (row[0],))
            return True

    def notifications(self, owner_id, limit=20, offset=0):
        """Materialize report-ready events on polling, independently of report commits.

        Missing events are retried on the next poll. Unique report IDs make
        repeated requests and process restarts safe without duplicate delivery.
        """
        with self.connection() as conn:
            now = self.clock()
            conn.execute(
                "INSERT INTO notifications(id,owner_id,report_id,created_at) "
                "SELECT 'notification_' || r.id,r.owner_id,r.id,r.created_at FROM reports r "
                "JOIN history h ON h.report_id=r.id WHERE r.owner_id=? "
                "AND r.deleted_at IS NULL AND h.deleted_at IS NULL AND r.expires_at>? "
                "ON CONFLICT(report_id) DO NOTHING", (owner_id, now))
            active = (" FROM notifications n JOIN reports r ON r.id=n.report_id "
                      "WHERE n.owner_id=? AND r.deleted_at IS NULL AND r.expires_at>?")
            unread = conn.execute("SELECT COUNT(*)" + active + " AND n.read_at IS NULL", (owner_id, now)).fetchone()[0]
            rows = conn.execute("SELECT n.*,r.payload" + active +
                                " ORDER BY n.created_at DESC,n.id DESC LIMIT ? OFFSET ?",
                                (owner_id, now, limit + 1, offset)).fetchall()
            items = [{"notification_id": row["id"], "type": "report_ready", "report_id": row["report_id"],
                      "branch": json.loads(row["payload"])["branch"], "created_at": iso(row["created_at"]),
                      "read_at": iso(row["read_at"]) if row["read_at"] is not None else None}
                     for row in rows[:limit]]
            return {"items": items, "unread_count": unread, "next_offset": offset + limit if len(rows) > limit else None}

    def read_notification(self, notification_id, owner_id):
        with self.connection() as conn:
            result = conn.execute(
                "UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND owner_id=? "
                "AND report_id IN (SELECT id FROM reports WHERE deleted_at IS NULL AND expires_at>?)",
                (self.clock(), notification_id, owner_id, self.clock()))
            return result.rowcount > 0

    def create_share(self, report_id, owner_id):
        with self.connection() as conn:
            row = conn.execute("SELECT expires_at FROM reports WHERE id=? AND owner_id=? AND deleted_at IS NULL AND expires_at>?", (report_id, owner_id, self.clock())).fetchone()
            if not row:
                return None
            token, sid, now = secrets.token_urlsafe(32), secrets.token_hex(16), self.clock()
            expiry = min(row[0], now + self.share_days * 86400)
            conn.execute("INSERT INTO shares VALUES (?,?,?,?,?,?,NULL)", (sid, owner_id, report_id, token_hash(token), now, expiry))
            return {"share_id": sid, "token": token, "expires_at": iso(expiry)}

    def shares(self, report_id, owner_id):
        if not self.report(report_id, owner_id):
            return None
        with self.connection() as conn:
            return [{"share_id": r["id"], "created_at": iso(r["created_at"]), "expires_at": iso(r["expires_at"]),
                     "revoked_at": iso(r["revoked_at"]) if r["revoked_at"] else None}
                    for r in conn.execute("SELECT * FROM shares WHERE report_id=? AND owner_id=? ORDER BY created_at DESC", (report_id, owner_id))]

    def revoke_share(self, share_id, owner_id):
        with self.connection() as conn:
            result = conn.execute("UPDATE shares SET revoked_at=? WHERE id=? AND owner_id=?", (self.clock(), share_id, owner_id))
            return result.rowcount > 0

    def shared_report(self, token):
        with self.connection() as conn:
            row = conn.execute("SELECT report_id FROM shares WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?", (token_hash(token), self.clock())).fetchone()
            data = self._report(conn, row[0]) if row else None
            if not data:
                return None
            allowed = ("branch", "generated_at", "counts", "score", "aspects", "summary", "negative_rate_interval", "warnings", "versions", "review_period", "source_mode", "analysis_seconds", "sources", "sentiment_distribution", "rating_distribution")
            return {k: data[k] for k in allowed if k in data}

    def cleanup(self):
        with self.connection() as conn:
            now = self.clock()
            raw = conn.execute("DELETE FROM evidence WHERE expires_at<=?", (now,)).rowcount
            checkpoints = conn.execute("DELETE FROM checkpoints WHERE expires_at<=?", (now,)).rowcount
            reports = conn.execute("DELETE FROM reports WHERE expires_at<=?", (now,)).rowcount
            return {"evidence": raw, "checkpoints": checkpoints, "reports": reports}


class WorkerLock:
    """OS lock released on crash, protecting the existing single-worker design."""
    def __init__(self, database_path):
        self.path = str(database_path) + ".worker.lock"
        self.file = None

    def acquire(self):
        self.file = open(self.path, "a+b")
        self.file.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt
                if self.file.read(1) == b"":
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("Another DineLens worker is using this database") from exc

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
