"""Opt-in browser E2E against the real API and analysis using fixed source rows.

Run with DINELENS_BROWSER_TESTS=1 and an installed Edge/Chrome or Chromium.
"""
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

import uvicorn

from .app import create_app
from .test_analysis import BRANCH
from .test_integration import FixedCollector


@unittest.skipUnless(os.getenv("DINELENS_BROWSER_TESTS") == "1", "Opt-in browser integration test")
class BrowserTests(unittest.TestCase):
    def test_search_report_share_revoke_and_delete(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            app = create_app(database_path=Path(directory) / "browser.db", collector=FixedCollector(),
                             searcher=lambda q, city, source, **kwargs: [BRANCH], origins=[origin])
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(server.started)
                with sync_playwright() as p:
                    browser = p.chromium.launch(channel=os.getenv("DINELENS_BROWSER_CHANNEL", "msedge"), headless=True)
                    try:
                        context = browser.new_context()
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(origin + "/web/analysis.html")
                        page.locator("#restaurant").fill("Test")
                        page.locator("#city").fill("杭州")
                        page.locator("#search-button").click()
                        page.get_by_role("button", name="确认此分店并分析").click()
                        page.locator("#report-link").wait_for(state="visible")
                        page.get_by_role("button", name="标记已读").wait_for()
                        page.get_by_role("button", name="标记已读").click()
                        page.locator("#notifications").get_by_text("已读", exact=True).wait_for()
                        page.reload()
                        page.locator("#notifications").get_by_text("已读", exact=True).wait_for()
                        page.locator("#notifications a").click()
                        page.get_by_role("heading", name="评论原文依据").wait_for()
                        self.assertIn("有效评分 1 条", page.locator("#report").inner_text())
                        self.assertIn("自动化测试样本", page.locator("#report").inner_text())
                        if os.getenv("DINELENS_SCREENSHOT_PATH"):
                            screenshot = Path(os.environ["DINELENS_SCREENSHOT_PATH"])
                            screenshot.parent.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(screenshot), full_page=True)
                        page.locator("#create-share").click()
                        link = page.locator("#share-result a")
                        link.wait_for()
                        share_url = link.get_attribute("href")
                        shared = browser.new_context()
                        shared_page = shared.new_page()
                        shared_page.goto(share_url)
                        shared_page.get_by_text("只读分享报告", exact=True).wait_for()
                        self.assertFalse(shared_page.locator("#sharing").is_visible())
                        page.get_by_role("button", name="撤销分享").click()
                        page.get_by_text("已撤销", exact=False).wait_for()
                        shared_page.reload()
                        shared_page.get_by_text("分享链接无效、已撤销、已过期或报告已删除。", exact=True).wait_for()
                        shared.close()
                        page.goto(origin + "/web/analysis.html")
                        page.get_by_role("button", name="删除历史").wait_for()
                        page.once("dialog", lambda dialog: dialog.accept())
                        page.get_by_role("button", name="删除历史").click()
                        page.get_by_text("当前浏览器还没有历史报告。", exact=True).wait_for()
                        page.get_by_text("暂无报告通知。", exact=True).wait_for()
                        self.assertEqual(errors, [])
                        context.close()
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(10)
