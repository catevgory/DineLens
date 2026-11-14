import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from .analysis import AnalysisEngine, calculate_score, normalized_text, rule_assessment, sentiment, wilson_interval
from .local_model import OllamaAssessor
from .screening import HybridAssessor
from .settings import Settings


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
        self.assertEqual(result["counts"], {"received": 5, "unique": 3, "duplicates": 1, "rejected": 1, "retained": 2, "flagged": 1, "uncertain": 0, "text_eligible": 2, "rating_eligible": 1})
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

    def test_rule_uncertain_cases(self):
        self.assertEqual(rule_assessment("一般般")["decision"], "uncertain")
        self.assertEqual(rule_assessment("服务一般")["decision"], "uncertain")
        self.assertEqual(rule_assessment("食物很好")["decision"], "retained")
        self.assertEqual(rule_assessment("出售好评，联系微信 abc")["decision"], "flagged")
        routed = rule_assessment("这家店可以帮忙写好评，加微信返现")
        self.assertEqual(routed["decision"], "uncertain")
        self.assertIn("SUSPICIOUS_PROMOTION", routed["reasons"])
        complaint = rule_assessment("我怀疑这家店在刷好评，服务很差。")
        self.assertEqual(complaint["decision"], "retained")

    def test_ollama_screens_uncertain_review(self):
        from unittest.mock import Mock
        session = Mock()
        session.post.return_value.json.return_value = {"message": {"content":
            '{"decision":"retained","reasons":[],"spans":[{"aspect":"other","polarity":"neutral","quote_id":0}]}'}}
        result = OllamaAssessor("test", session=session)("一般般")
        self.assertEqual(result["decision"], "retained")
        self.assertEqual(result["spans"][0]["quote"], "一般般")
        session.post.reset_mock()
        session.post.return_value.json.return_value = {"message": {"content":
            '{"decision":"flagged","reasons":["PROMOTIONAL_REVIEW_OFFER"],"spans":[]}'}}
        result = OllamaAssessor("test", session=session)("服务一般")
        self.assertEqual(result["decision"], "flagged")
        self.assertEqual(result["reasons"], ["PROMOTIONAL_REVIEW_OFFER"])

    def test_uncertain_is_excluded_without_llm(self):
        result = AnalysisEngine(HybridAssessor(llm=None, enabled=False)).analyse(
            [row("一般般", "4")], BRANCH)
        self.assertEqual(result["counts"]["retained"], 0)
        self.assertEqual(result["counts"]["uncertain"], 1)
        self.assertEqual(result["counts"]["rating_eligible"], 0)
        self.assertIsNone(result["score"]["value"])
        self.assertIn("UNCERTAIN_REVIEWS_EXCLUDED", result["warnings"])
        self.assertIn("LLM_DISABLED", result["warnings"])
        self.assertEqual(result["screening"]["assessments"][0]["method"], "rules_fallback")

    def test_hybrid_uses_llm_only_for_uncertain(self):
        class FakeLLM:
            def __init__(self):
                self.calls = []

            def screen(self, text, rule):
                self.calls.append(text)
                return {"decision": "retained", "reasons": [],
                        "spans": [{"quote": "一般般", "aspect": "other", "polarity": "neutral"}]}

        llm = FakeLLM()
        engine = AnalysisEngine(HybridAssessor(llm=llm, enabled=True))
        result = engine.analyse([row("食物很好", "5", "clear"), row("一般般", "4", "hard")], BRANCH)
        self.assertEqual(llm.calls, ["一般般"])
        self.assertEqual(result["counts"]["retained"], 2)
        self.assertEqual(result["counts"]["uncertain"], 0)
        self.assertEqual(result["screening"]["assessments"][0]["method"], "rules")
        self.assertEqual(result["screening"]["assessments"][1]["method"], "llm")

    def test_hybrid_marks_uncertain_review_flagged(self):
        class FakeLLM:
            def screen(self, text, rule):
                return {"decision": "flagged", "reasons": ["MODEL_FLAGGED"], "spans": []}

        result = AnalysisEngine(HybridAssessor(llm=FakeLLM(), enabled=True)).analyse(
            [row("一般般", "4")], BRANCH)
        self.assertEqual(result["counts"]["flagged"], 1)
        self.assertEqual(result["counts"]["uncertain"], 0)
        self.assertEqual(result["counts"]["rating_eligible"], 0)

    def test_suspicious_promotion_is_routed_to_llm(self):
        class FakeLLM:
            def __init__(self):
                self.calls = []

            def screen(self, text, rule):
                self.calls.append(text)
                return {"decision": "flagged", "reasons": ["PROMOTIONAL_REVIEW_OFFER"], "spans": []}

        llm = FakeLLM()
        text = "这家店可以帮忙写好评，加微信返现"
        result = AnalysisEngine(HybridAssessor(llm=llm, enabled=True)).analyse(
            [row(text, "5")], BRANCH)
        self.assertEqual(llm.calls, [normalized_text(text)])
        self.assertEqual(result["counts"]["flagged"], 1)
        self.assertEqual(result["counts"]["retained"], 0)
        self.assertEqual(result["screening"]["assessments"][0]["method"], "llm")

    def test_hybrid_falls_back_and_opens_circuit(self):
        class DownLLM:
            def __init__(self):
                self.calls = 0

            def screen(self, text, rule):
                self.calls += 1
                raise RuntimeError("down")

        llm = DownLLM()
        result = AnalysisEngine(HybridAssessor(llm=llm, enabled=True)).analyse(
            [row("一般般", "4", "a"), row("服务一般", "4", "b")], BRANCH)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(result["counts"]["uncertain"], 2)
        self.assertIn("LLM_UNAVAILABLE", result["warnings"])

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(path)
            self.assertFalse(settings.llm_enabled)
            settings.update(llm_enabled=True, llm_model="qwen2.5:3b",
                            llm_url="http://127.0.0.1:11434")
            loaded = Settings(path)
            self.assertTrue(loaded.llm_enabled)
            self.assertEqual(loaded.llm_model, "qwen2.5:3b")
            self.assertEqual(loaded.llm_url, "http://127.0.0.1:11434")
            loaded.update(llm_enabled=False)
            self.assertFalse(Settings(path).llm_enabled)
