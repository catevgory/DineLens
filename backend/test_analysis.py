import unittest
from datetime import datetime, timezone
from .analysis import AnalysisEngine, calculate_score, sentiment, wilson_interval
from .local_model import OllamaAssessor


def row(text="食物很好，但服务很慢。", rating="4", rid="r1", **extra):
    return {"source": "amap", "poi_id": "p1", "content": text, "rating": rating, "review_id": rid, **extra}


BRANCH = {"id": "p1", "source": "amap", "name": "Test", "city": "杭州", "address": "Test address"}


class AnalysisTests(unittest.TestCase):
    def test_score_boundaries(self):
        for n in (0, 1, 4, 5):
            with self.subTest(n=n):
                score = calculate_score([row(rid=str(i)) for i in range(n)])
                self.assertEqual(score["valid_count"], n)
                self.assertEqual(score["sample_status"], "none" if not n else ("low" if n < 5 else "adequate"))
                self.assertEqual(score["value"] is None, n == 0)

    def test_shrinkage_examples_and_time_decay(self):
        score = calculate_score([row(rating=5, rid=str(i)) for i in range(3)], method="bayesian_v1")
        self.assertAlmostEqual(score["value"], 50 / 13)
        rows = [row(rating=5, date="2020-01-01"), row(rating=1, rid="r2", date="2026-11-13")]
        value = calculate_score(rows, now=datetime(2026, 11, 13, tzinfo=timezone.utc))
        self.assertLess(value["value"], 3.5)
        self.assertAlmostEqual(calculate_score(rows, method="mean_v1")["value"], 3)

    def test_mixed_aspects_quotes_and_counts(self):
        result = AnalysisEngine().analyse([row(), row(), row("Selling fake reviews. Contact our agency.", 5, "r2"), row("环境很好", None, "r3"), row(poi_id="other", rid="r4")], BRANCH)
        self.assertEqual(result["counts"], {"received": 5, "unique": 3, "duplicates": 1, "rejected": 1, "retained": 2, "flagged": 1, "text_eligible": 2, "rating_eligible": 1})
        labels = {a["aspect"]: a for a in result["aspects"]}
        self.assertEqual(labels["food"]["positive"], 1)
        self.assertEqual(labels["service"]["negative"], 1)
        self.assertEqual(sum(result["rating_distribution"].values()), result["score"]["valid_count"])
        for quote in result["summary"]:
            text = result["evidence"][quote["review_id"]]["text"]
            self.assertEqual(text[quote["start"]:quote["end"]], quote["quote"])

    def test_missing_and_invalid_ratings(self):
        rows = [row(rating=x, rid=str(i)) for i, x in enumerate((None, "", "NaN", True, "99", "bad"))]
        result = AnalysisEngine().analyse(rows, BRANCH)
        self.assertIsNone(result["score"]["value"])
        self.assertTrue(result["summary"])

    def test_negation_and_legitimate_complaint(self):
        self.assertEqual(sentiment("食物不好"), "negative")
        self.assertEqual(sentiment("the service was not good"), "negative")
        result = AnalysisEngine().analyse([row("Terrible food. Very bad service.", 1)], BRANCH)
        self.assertEqual(result["counts"]["flagged"], 0)
        criticism = AnalysisEngine().analyse([row("They buy fake reviews. Terrible food.", 1)], BRANCH)
        self.assertEqual(criticism["counts"]["flagged"], 0)

    def test_unknown_scale_and_empty_input(self):
        self.assertIsNone(calculate_score([row(source="unknown")])["value"])
        self.assertIn("NO_USABLE_EVIDENCE", AnalysisEngine().analyse([], BRANCH)["warnings"])
        self.assertIsNone(wilson_interval(0, 0))
        self.assertAlmostEqual(wilson_interval(5, 10)["lower"], 0.23658959)

    def test_bad_model_evidence_and_cancellation(self):
        engine = AnalysisEngine(assessor=lambda text: {"decision": "retained", "reasons": [], "spans": [{"quote": "invented", "aspect": "food", "polarity": "positive"}]})
        with self.assertRaises(ValueError):
            engine.analyse([row()], BRANCH)
        def cancel():
            raise InterruptedError()
        with self.assertRaises(InterruptedError):
            AnalysisEngine().analyse([row()], BRANCH, cancel_check=cancel)

    def test_ollama_adapter_validates_schema(self):
        from unittest.mock import Mock
        session = Mock()
        session.post.return_value.json.return_value = {"message": {"content": '{"spans":[{"aspect":"food","polarity":"positive","quote_id":0}]}'}}
        self.assertEqual(OllamaAssessor("test", session=session)("food good")["decision"], "retained")
        choices = session.post.call_args.kwargs["json"]["format"]["$defs"]["AspectOpinion"]["properties"]["quote_id"]["enum"]
        self.assertEqual(choices, [0])
        session.post.return_value.json.return_value = {"message": {"content": '{"spans":[{"aspect":"food","polarity":"positive","quote_id":999}]}'}}
        with self.assertRaises(RuntimeError):
            OllamaAssessor("test", session=session)("different text")

    def test_ollama_uses_conservative_screening_and_preserves_complaints(self):
        from unittest.mock import Mock
        session = Mock()
        session.post.return_value.json.return_value = {"message": {"content": '{"spans":[]}'}}
        assessor = OllamaAssessor("test", session=session)
        for text in ("等了一个小时，菜还是凉的，不会再来。", "我怀疑这家店在刷好评，服务很差。", "The food was not good and the staff ignored us."):
            self.assertEqual(assessor(text)["decision"], "retained")
        session.post.reset_mock()
        self.assertEqual(assessor("Selling fake reviews. Contact our agency.")["decision"], "flagged")
        session.post.assert_not_called()

    def test_ollama_retries_invalid_evidence_once(self):
        from unittest.mock import Mock
        bad, good, session = Mock(), Mock(), Mock()
        bad.json.return_value = {"message": {"content": '{"spans":[{"aspect":"food","polarity":"positive","quote_id":999}]}'}}
        good.json.return_value = {"message": {"content": '{"spans":[{"aspect":"food","polarity":"positive","quote_id":0}]}'}}
        session.post.side_effect = [bad, good]
        result = OllamaAssessor("test", session=session)("good food")
        self.assertEqual(result["spans"][0]["quote"], "good food")
        self.assertEqual(session.post.call_count, 2)
        session.post.reset_mock(side_effect=True)
        session.post.return_value = bad
        with self.assertRaises(RuntimeError):
            OllamaAssessor("test", session=session)("good food")
        self.assertEqual(session.post.call_count, 2)

    def test_ollama_restores_long_source_quote_from_short_index(self):
        from unittest.mock import Mock
        text = "食物" + "非常" * 600 + "好吃"
        session = Mock()
        session.post.return_value.json.return_value = {"message": {"content": '{"spans":[{"aspect":"food","polarity":"positive","quote_id":0}]}'}}
        result = OllamaAssessor("test", session=session)(text)
        self.assertEqual(result["spans"][0]["quote"], text)
