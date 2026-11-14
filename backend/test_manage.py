from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .manage import main
from .job_manager import Job, JobManager
from .storage import Storage
from dataclasses import asdict
from .test_analysis import row, BRANCH
from .test_integration import wait, FixedCollector
from .analysis import AnalysisEngine


class ManageTests(unittest.TestCase):
    def test_migrate_reports_actual_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with patch.dict("os.environ", {"DINELENS_DB_PATH": str(Path(directory) / "test.db")}), redirect_stdout(output):
                main(["migrate"])
            self.assertEqual(json.loads(output.getvalue())["schema_version"], 2)

    def test_jsonl_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "reviews.jsonl"
            output_path = Path(directory) / "report.json"
            input_path.write_text(json.dumps(row(), ensure_ascii=False) + "\n", encoding="utf-8")
            with patch.dict("os.environ", {"DINELENS_ANALYSIS_MODE": "rules"}), redirect_stdout(StringIO()):
                main(["analyse", str(input_path), "--source", "amap", "--poi-id", "p1", "--store", "Test", "--city", "杭州", "--output", str(output_path)])
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["source_mode"], "file")
            self.assertEqual(report["score"]["valid_count"], 1)

    def test_operational_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            store = Storage(path)
            store.save_job(asdict(Job("retry_job", "Test", state="failed", branch=BRANCH)))
            with patch.dict("os.environ", {"DINELENS_DB_PATH": str(path)}), redirect_stdout(StringIO()):
                main(["retry", "retry_job", "--reason", "model configuration corrected"])
            manager = JobManager(storage=store, collector=FixedCollector(), analyser=AnalysisEngine().analyse)
            manager.start_worker()
            try:
                self.assertEqual(wait(manager, "retry_job")["state"], "done")
                self.assertNotIn("operator_note", manager.get_job("retry_job"))
            finally:
                manager.shutdown()
