from dataclasses import asdict
from pathlib import Path
import sqlite3
import tempfile
import unittest

from .analysis import AnalysisEngine
from .job_manager import Job
from .storage import Storage
from .test_analysis import BRANCH, row


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1800000000.0
        self.store = Storage(Path(self.temp.name) / "test.db", clock=lambda: self.now)
        self.token, self.owner, _ = self.store.create_owner()

    def complete(self, job_id="job1"):
        job = Job(job_id, "Test", owner_id=self.owner, branch=BRANCH, state="analysing")
        self.store.save_job(asdict(job))
        report = AnalysisEngine().analyse([row()], BRANCH)
        completed = {**asdict(job), "state": "done"}
        return self.store.complete(completed, report)

    def test_migration_replay_and_unique_completion(self):
        self.store.migrate()
        rid = self.complete()
        report = AnalysisEngine().analyse([row()], BRANCH)
        self.assertEqual(self.store.complete({"id": "job1", "owner_id": self.owner}, report), rid)
        self.assertEqual(len(self.store.history(self.owner)["items"]), 1)
        self.assertTrue(self.store.report(rid, self.owner)["summary"][0]["quote"])
        self.assertIsNone(self.store.report(rid, "another-owner"))

    def test_atomic_rollback(self):
        job = asdict(Job("job1", "Test", owner_id=self.owner, state="analysing"))
        self.store.save_job(job)
        with self.store.connection() as conn:
            conn.execute("CREATE TRIGGER fail_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.complete({**job, "state": "done"}, AnalysisEngine().analyse([row()], BRANCH))
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 0)
        self.assertEqual(self.store.load_jobs()[0]["state"], "analysing")

    def test_revoke_and_delete(self):
        rid = self.complete()
        share = self.store.create_share(rid, self.owner)
        shared = self.store.shared_report(share["token"])
        self.assertNotIn("screening", shared)
        self.assertNotIn("report_id", shared)
        self.assertTrue(self.store.revoke_share(share["share_id"], self.owner))
        self.assertIsNone(self.store.shared_report(share["token"]))
        share2 = self.store.create_share(rid, self.owner)
        hid = self.store.history(self.owner)["items"][0]["history_id"]
        self.assertFalse(self.store.delete_history(hid, "other"))
        self.assertTrue(self.store.delete_history(hid, self.owner))
        self.assertTrue(self.store.delete_history(hid, self.owner))
        self.assertIsNone(self.store.report(rid, self.owner))
        self.assertIsNone(self.store.shared_report(share2["token"]))

    def test_retention_and_checkpoint_expiry(self):
        rid = self.complete()
        self.store.checkpoint("job1", [row()])
        share = self.store.create_share(rid, self.owner)
        self.now += 31 * 86400
        self.assertIsNone(self.store.shared_report(share["token"]))
        self.now += 60 * 86400
        data = self.store.report(rid, self.owner)
        self.assertEqual(data["summary"][0]["evidence_status"], "expired")
        self.assertIsNone(data["summary"][0]["quote"])
        self.assertIsNone(self.store.checkpoint("job1"))
        self.assertEqual(self.store.cleanup()["evidence"], 1)
        self.now += 300 * 86400
        self.assertEqual(self.store.cleanup()["reports"], 1)
        self.assertEqual(self.store.history(self.owner)["items"], [])

    def test_bad_evidence_rejected_before_write(self):
        job = asdict(Job("bad", "Test", owner_id=self.owner))
        self.store.save_job(job)
        report = AnalysisEngine().analyse([row()], BRANCH)
        report["summary"][0]["quote"] = "invented"
        with self.assertRaises(ValueError):
            self.store.complete(job, report)

    def test_notifications_persist_read_state_and_disappear_with_report(self):
        rid = self.complete()
        data = self.store.notifications(self.owner)
        self.assertEqual(data["unread_count"], 1)
        notice = data["items"][0]
        self.assertEqual(notice["report_id"], rid)
        self.assertEqual(self.store.notifications("other")["items"], [])
        self.assertFalse(self.store.read_notification(notice["notification_id"], "other"))
        self.assertTrue(self.store.read_notification(notice["notification_id"], self.owner))
        read_at = self.store.notifications(self.owner)["items"][0]["read_at"]
        self.now += 5
        self.assertTrue(self.store.read_notification(notice["notification_id"], self.owner))
        restarted = Storage(self.store.path, clock=lambda: self.now)
        self.assertEqual(restarted.notifications(self.owner)["unread_count"], 0)
        self.assertEqual(restarted.notifications(self.owner)["items"][0]["read_at"], read_at)
        self.store.delete_history("history_job1", self.owner)
        self.assertEqual(self.store.notifications(self.owner)["items"], [])
        self.assertFalse(self.store.read_notification(notice["notification_id"], self.owner))

    def test_notification_failure_does_not_rollback_report_and_retry_is_unique(self):
        with self.store.connection() as conn:
            conn.execute("CREATE TRIGGER fail_notice BEFORE INSERT ON notifications BEGIN SELECT RAISE(ABORT, 'notice failure'); END")
        rid = self.complete()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.notifications(self.owner)
        self.assertIsNotNone(self.store.report(rid, self.owner))
        self.assertEqual(self.store.load_jobs()[0]["state"], "done")
        with self.store.connection() as conn:
            conn.execute("DROP TRIGGER fail_notice")
        for _ in range(3):
            self.assertEqual(len(self.store.notifications(self.owner)["items"]), 1)
        self.now += 366 * 86400
        self.assertEqual(self.store.notifications(self.owner)["items"], [])
        self.store.cleanup()
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)

    def test_existing_v1_database_migrates_without_losing_reports(self):
        rid = self.complete()
        with self.store.connection() as conn:
            conn.execute("DROP TABLE notifications")
            conn.execute("DELETE FROM schema_versions WHERE version=2")
        self.store.migrate()
        self.assertEqual(self.store.notifications(self.owner)["items"][0]["report_id"], rid)
        self.assertEqual(len(self.store.history(self.owner)["items"]), 1)

    def test_notification_concurrent_polling_and_pagination(self):
        from concurrent.futures import ThreadPoolExecutor
        for job_id in ("one", "two", "three"):
            self.complete(job_id)
            self.now += 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.notifications(self.owner, limit=2), range(8)))
        for result in results:
            self.assertEqual(result["unread_count"], 3)
            self.assertEqual(len(result["items"]), 2)
            self.assertEqual(result["next_offset"], 2)
        first = results[0]
        last = self.store.notifications(self.owner, limit=2, offset=first["next_offset"])
        self.assertEqual(len(last["items"]), 1)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(len({n["notification_id"] for n in first["items"] + last["items"]}), 3)
