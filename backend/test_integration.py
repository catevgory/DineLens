from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from .analysis import AnalysisEngine
from .app import COOKIE, create_app
from .job_manager import Job, JobManager
from .pipeline import Collector
from .storage import Storage
from .test_analysis import BRANCH, row


class FixedCollector:
    source_mode = "fixture"

    def __call__(self, job, check):
        check()
        return [row(), row("服务友好，环境舒适。", None, "r2")]


def wait(manager, jid, states=("done", "failed", "cancelled", "needs_operator")):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get_job(jid)
        if job["state"] in states:
            return job
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for job")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"

    def app(self, collector=None):
        return create_app(database_path=self.path, collector=collector or FixedCollector(),
                          searcher=lambda q, city, source, **kwargs: [BRANCH], origins=["http://testserver"])

    def session(self, client):
        response = client.post("/owner-session", headers={"Origin": "http://testserver"})
        self.assertIn(response.status_code, (200, 201))
        return {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}

    def create(self, client, headers, key=None):
        branch = client.get("/branches", params={"q": "Test", "city": "杭州"}).json()["items"][0]
        payload = {"store": "Test", "branch_id": branch["branch_id"], "branch_confirmed": True}
        response = client.post("/jobs", headers={**headers, **({"Idempotency-Key": key} if key else {})}, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), payload

    def test_full_flow_ownership_sharing_and_deletion(self):
        app = self.app()
        with TestClient(app) as client:
            headers = self.session(client)
            owner_token = client.cookies.get(COOKIE)
            created, _ = self.create(client, headers)
            done = wait(app.state.manager, created["id"])
            self.assertEqual(done["state"], "done", done)
            rid = done["report_id"]
            self.assertEqual(client.get("/reports/" + rid).json()["source_mode"], "fixture")
            share = client.post(f"/reports/{rid}/shares", headers=headers).json()
            self.assertTrue(share["share_url"].startswith("http://testserver/web/report.html#share="))
            bearer = {"Authorization": "Bearer " + share["share_url"].split("=", 1)[1]}
            client.cookies.clear()
            self.assertEqual(client.get("/reports/" + rid).status_code, 401)
            self.session(client)
            self.assertEqual(client.get("/reports/" + rid).status_code, 404)
            self.assertEqual(client.get("/jobs/" + created["id"]).status_code, 404)
            self.assertEqual(client.get("/history").json()["items"], [])
            public = client.get("/shared-report", headers=bearer)
            self.assertEqual(public.status_code, 200)
            self.assertNotIn("screening", public.json())
            client.cookies.clear()
            client.cookies.set(COOKIE, owner_token)
            hid = client.get("/history").json()["items"][0]["history_id"]
            self.assertEqual(client.delete("/history/" + hid, headers=headers).status_code, 204)
            self.assertEqual(client.get("/shared-report", headers=bearer).status_code, 404)
            self.assertIsNone(client.get("/jobs/" + created["id"]).json()["report"])

    def test_csrf_origin_and_validation(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.post("/owner-session", headers={"Origin": "https://evil.invalid"}).status_code, 403)
            headers = self.session(client)
            self.assertEqual(client.post("/jobs", json={"store": "Test"}).status_code, 403)
            self.assertEqual(client.get("/branches", params={"q": "Test", "city": " "}).status_code, 422)
            branch = client.get("/branches", params={"q": "Test", "city": "杭州"}).json()["items"][0]
            self.assertEqual(client.post("/jobs", headers=headers, json={"store": "Test", "branch_id": branch["branch_id"]}).status_code, 422)

    def test_idempotency_and_read_only_polling(self):
        app = self.app()
        with TestClient(app) as client:
            headers = self.session(client)
            job, payload = self.create(client, headers, "request-one")
            wait(app.state.manager, job["id"])
            for _ in range(3):
                self.assertEqual(client.post("/jobs", headers={**headers, "Idempotency-Key": "request-one"}, json=payload).json()["id"], job["id"])
                client.get("/jobs/" + job["id"])
            self.assertEqual(len(client.get("/history").json()["items"]), 1)
            self.assertEqual(client.post("/jobs", headers={**headers, "Idempotency-Key": "request-one"}, json={**payload, "store": "Changed"}).status_code, 409)

    def test_completed_report_survives_restart(self):
        app = self.app()
        with TestClient(app) as client:
            headers = self.session(client)
            token = client.cookies.get(COOKIE)
            job, _ = self.create(client, headers)
            result = wait(app.state.manager, job["id"])
        with TestClient(self.app()) as client:
            client.cookies.set(COOKIE, token)
            self.assertEqual(client.get("/jobs/" + job["id"]).json()["state"], "done")
            self.assertEqual(client.get("/reports/" + result["report_id"]).status_code, 200)
            self.assertEqual(len(client.get("/history").json()["items"]), 1)

    def test_notification_api_ownership_csrf_and_read_state(self):
        app = self.app()
        with TestClient(app) as client:
            self.assertEqual(client.get("/notifications").status_code, 401)
            headers = self.session(client)
            token = client.cookies.get(COOKIE)
            job, _ = self.create(client, headers)
            done = wait(app.state.manager, job["id"])
            data = client.get("/notifications?limit=1").json()
            self.assertEqual(data["unread_count"], 1)
            notice = data["items"][0]
            self.assertEqual(notice["report_id"], done["report_id"])
            url = "/notifications/" + notice["notification_id"] + "/read"
            self.assertEqual(client.post(url).status_code, 403)
            client.cookies.clear()
            other_headers = self.session(client)
            self.assertEqual(client.get("/notifications").json()["items"], [])
            self.assertEqual(client.post(url, headers=other_headers).status_code, 404)
            client.cookies.clear()
            client.cookies.set(COOKIE, token)
            self.assertEqual(client.post(url, headers=headers).status_code, 204)
            self.assertEqual(client.post(url, headers=headers).status_code, 204)
            self.assertEqual(client.get("/notifications").json()["unread_count"], 0)
            self.assertEqual(client.get("/notifications?limit=0").status_code, 422)

    def test_pipeline_cancellation_discards_report(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingCollector(FixedCollector):
            def __call__(self, job, check):
                entered.set()
                release.wait(3)
                check()
                return super().__call__(job, check)
        app = self.app(BlockingCollector())
        with TestClient(app) as client:
            headers = self.session(client)
            job, _ = self.create(client, headers)
            self.assertTrue(entered.wait(2))
            self.assertEqual(client.post(f"/jobs/{job['id']}/cancel", headers=headers).status_code, 200)
            release.set()
            self.assertEqual(wait(app.state.manager, job["id"])["state"], "cancelled")
            self.assertEqual(client.get("/history").json()["items"], [])

    def test_model_failure_produces_no_report_or_history(self):
        def broken_model(text):
            raise RuntimeError("invalid model evidence")
        app = create_app(database_path=self.path, collector=FixedCollector(), engine=AnalysisEngine(broken_model),
                         searcher=lambda q, city, source, **kw: [BRANCH], origins=["http://testserver"])
        with TestClient(app) as client:
            headers = self.session(client)
            job, _ = self.create(client, headers)
            failed = wait(app.state.manager, job["id"])
            self.assertEqual(failed["state"], "failed")
            self.assertIsNone(failed["report_id"])
            self.assertEqual(client.get("/history").json()["items"], [])

    def test_recover_collected_checkpoint(self):
        storage = Storage(self.path)
        job = Job("recovered", "Test", owner_id="owner", branch=BRANCH, state="analysing")
        storage.save_job(asdict(job))
        storage.checkpoint(job.id, [row()])
        def unexpected_collect(job, check):
            raise AssertionError("Collected checkpoint should be reused")
        manager = JobManager(storage=storage, collector=unexpected_collect, analyser=AnalysisEngine().analyse)
        manager.start_worker()
        try:
            self.assertEqual(wait(manager, job.id)["state"], "done")
        finally:
            manager.shutdown()

    def test_exclusive_worker_and_unconfigured_branch(self):
        first = JobManager(storage=Storage(self.path), collector=Collector(), analyser=AnalysisEngine().analyse)
        first.start_worker()
        second = JobManager(storage=Storage(self.path))
        try:
            with self.assertRaises(RuntimeError):
                second.start_worker()
            job = first.create_job("Legacy store-only request")
            self.assertEqual(wait(first, job["id"])["state"], "needs_operator")
        finally:
            first.shutdown()
