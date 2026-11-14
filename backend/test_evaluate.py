from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .analysis import AnalysisEngine
from .evaluate import main, metrics


class EvaluationTests(unittest.TestCase):
    def test_unclassified_predictions_count_as_misses(self):
        result = metrics(["positive", "negative"], ["positive", "error"], ["positive", "negative"])
        self.assertEqual(result["agreement"], 0.5)
        self.assertEqual(result["per_label"]["negative"]["fn"], 1)

    def test_configured_engine_sampling_and_zero_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.tsv", Path(directory) / "result.json"
            source.write_text("great food\t1\nbad service\t0\ndelicious\t1\nawful\t0\n", encoding="utf-8")
            arguments = ["--sentiment-tsv", str(source), "--output", str(output), "--engine", "configured", "--sample-per-label", "1"]
            with patch("backend.pipeline.configured_engine", return_value=AnalysisEngine()), redirect_stdout(StringIO()):
                main(arguments)
                first = json.loads(output.read_text(encoding="utf-8"))
                main(arguments)
                second = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(first["sentiment"]["selection"], second["sentiment"]["selection"])
            self.assertEqual(first["sentiment"]["engine"]["count"], 2)
            self.assertEqual(first["performance"]["runs"], 0)

    def test_model_failure_remains_visible_in_metrics(self):
        def fail(text):
            raise RuntimeError("model unavailable")
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.tsv", Path(directory) / "result.json"
            source.write_text("great food\t1\nbad service\t0\n", encoding="utf-8")
            with patch("backend.pipeline.configured_engine", return_value=AnalysisEngine(fail)), redirect_stdout(StringIO()):
                main(["--sentiment-tsv", str(source), "--output", str(output), "--engine", "configured"])
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["sentiment"]["engine"]["agreement"], 0)
            self.assertEqual(len(result["sentiment"]["errors"]), 2)
