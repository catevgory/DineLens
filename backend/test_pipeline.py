from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from .analysis import AnalysisEngine
from .job_manager import Job, JobCancelled, JobManager, NeedsOperator
from .pipeline import Collector
from .storage import Storage
from .test_analysis import BRANCH, row
from .test_integration import FixedCollector, wait


class PipelineTests(unittest.TestCase):
    def test_cancel_wins_over_inflight_model_failure(self):
        entered, release = threading.Event(), threading.Event()
        def model(text):
            entered.set()
            release.wait(2)
            raise RuntimeError("model timeout")
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "test.db")
            manager = JobManager(storage=store, collector=FixedCollector(), analyser=AnalysisEngine(model).analyse)
            manager.start_worker()
            try:
                job = manager.create_job("Test", branch=BRANCH, owner_id="owner")
                self.assertTrue(entered.wait(1))
                self.assertTrue(manager.request_cancel(job["id"])["ok"])
                release.set()
                self.assertEqual(wait(manager, job["id"])["state"], "cancelled")
                self.assertEqual(store.history("owner")["items"], [])
            finally:
                release.set()
                manager.shutdown()

    def test_existing_fetcher_receives_cancellation_callback(self):
        with patch("fetchers.browser_fetch.fetch_amap", return_value=[row()]) as fetch:
            callback = lambda: None
            rows = Collector()(Job("job", "Test", branch=BRANCH), callback)
            self.assertEqual(rows[0]["content"], row()["content"])
            self.assertIs(fetch.call_args.kwargs["cancel_check"], callback)
            self.assertFalse(fetch.call_args.kwargs["headed"])
        with patch("fetchers.browser_fetch.fetch_amap", return_value=[]):
            with self.assertRaises(NeedsOperator):
                Collector()(Job("job", "Test", branch=BRANCH), lambda: None)

    def test_fetcher_cancel_happens_before_browser_launch(self):
        from fetchers.browser_fetch import fetch_amap, fetch_baidu
        def cancel():
            raise JobCancelled()
        for fetch in (fetch_amap, fetch_baidu):
            with self.assertRaises(JobCancelled):
                fetch("p1", ".", cancel_check=cancel)

    def test_amap_login_redirect_keeps_browser_for_operator(self):
        from unittest.mock import Mock
        from fetchers.browser_fetch import _open_amap_detail
        page = Mock()
        detail = "https://www.amap.com/detail/p1"
        def navigate(*args, **kwargs):
            if page.goto.call_count == 1:
                page.url = "https://id.amap.com/user"
                raise RuntimeError("Navigation is interrupted by another navigation")
            page.url = detail
        page.goto.side_effect = navigate
        with patch("fetchers.browser_fetch._risk_help") as operator:
            _open_amap_detail(page, detail, True, ".")
            operator.assert_called_once_with(True, ".", page=page)
        self.assertEqual(page.goto.call_count, 2)
        page.goto.side_effect = RuntimeError("connection refused")
        with self.assertRaisesRegex(RuntimeError, "connection refused"):
            _open_amap_detail(page, detail, True, ".")

    def test_amap_search_fallback_preserves_existing_candidates(self):
        from unittest.mock import MagicMock
        from fetchers.browser_fetch import search_amap
        payload = {"data": {"poi_list": [{"id": "p1", "name": "Test", "cityname": "杭州"}]}}
        for captured in ([], [{"body": payload}]):
            with tempfile.TemporaryDirectory() as directory, \
                    patch("fetchers.browser_fetch.BrowserSession") as browser, \
                    patch("fetchers.browser_fetch._attach_capture", return_value=(captured, MagicMock())), \
                    patch("fetchers.browser_fetch._wait_risk", return_value=False), \
                    patch("fetchers.browser_fetch._detect_risk", return_value=False):
                request = browser.return_value.__enter__.return_value.context.request.get
                request.return_value.ok = True
                request.return_value.json.return_value = payload
                rows = search_amap("Test", "330100", directory)
                self.assertEqual(rows[0]["id"], "p1")
                self.assertEqual(request.call_count, 0 if captured else 1)
                if not captured:
                    self.assertIn("city=330100", request.call_args.args[0])

    def test_amap_search_fallback_keeps_challenge_handling(self):
        from unittest.mock import MagicMock
        from fetchers.browser_fetch import search_amap
        with tempfile.TemporaryDirectory() as directory, \
                patch("fetchers.browser_fetch.BrowserSession") as browser, \
                patch("fetchers.browser_fetch._attach_capture", return_value=([], MagicMock())), \
                patch("fetchers.browser_fetch._wait_risk", return_value=False), \
                patch("fetchers.browser_fetch._detect_risk", return_value=False), \
                patch("fetchers.browser_fetch._goto_json", return_value={"data": {"poi_list": []}}) as challenge:
            response = browser.return_value.__enter__.return_value.context.request.get.return_value
            response.ok = True
            response.json.return_value = {"need_recaptcha": True}
            self.assertEqual(search_amap("Test", "330100", directory), [])
            challenge.assert_called_once()

    def test_analysis_phase_cancel_and_done_conflict(self):
        entered, release = threading.Event(), threading.Event()
        from .analysis import rule_assessment
        def assess(text):
            entered.set()
            release.wait(2)
            return rule_assessment(text)
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(Path(directory) / "test.db")
            manager = JobManager(storage=store, collector=FixedCollector(), analyser=AnalysisEngine(assess).analyse)
            manager.start_worker()
            try:
                job = manager.create_job("Test", branch=BRANCH, owner_id="owner")
                self.assertTrue(entered.wait(1))
                self.assertEqual(manager.get_job(job["id"])["state"], "analysing")
                self.assertTrue(manager.request_cancel(job["id"])["ok"])
                release.set()
                self.assertEqual(wait(manager, job["id"])["state"], "cancelled")
                self.assertEqual(store.history("owner")["items"], [])
                next_job = manager.create_job("Test", branch=BRANCH, owner_id="owner")
                self.assertEqual(wait(manager, next_job["id"])["state"], "done")
                self.assertFalse(manager.request_cancel(next_job["id"])["ok"])
            finally:
                release.set()
                manager.shutdown()
