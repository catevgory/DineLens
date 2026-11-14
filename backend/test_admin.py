import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from admin.server import app


class AdminPanelTests(unittest.TestCase):
    def test_index_and_log_auth(self):
        with patch.dict(os.environ, {"DINELENS_ADMIN_TOKEN": "test-admin"}, clear=False):
            client = TestClient(app)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/logs", params={"source": "backend"}).status_code, 403)
            response = client.get("/api/logs", params={"source": "backend"},
                                  headers={"X-Admin-Token": "test-admin"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["source"], "backend")
            self.assertEqual(client.get("/api/logs", params={"source": "../secret"},
                                        headers={"X-Admin-Token": "test-admin"}).status_code, 422)
