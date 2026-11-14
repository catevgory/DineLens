"""Opt-in regression for failures discovered during real local inference."""
import os
import unittest

from .local_model import OllamaAssessor


@unittest.skipUnless(os.getenv("DINELENS_OLLAMA_TESTS") == "1", "Requires a running local Ollama model")
class LocalModelLiveTests(unittest.TestCase):
    def test_real_aspects_quotes_and_legitimate_complaints(self):
        assessor = OllamaAssessor(os.getenv("DINELENS_OLLAMA_MODEL", "qwen2.5:3b"),
                                  os.getenv("DINELENS_OLLAMA_URL", "http://127.0.0.1:11434"))
        cases = [
            ("食物很好，但服务很慢。", "retained", {("food", "positive", "食物很好"), ("service", "negative", "服务很慢")}),
            ("等了一个小时，菜还是凉的，不会再来。", "retained", {("service", "negative", "等了一个小时"), ("food", "negative", "菜还是凉的")}),
            ("价格合理，环境干净，很满意。", "retained", {("price", "positive", "价格合理"), ("atmosphere", "positive", "环境干净")}),
            ("我怀疑这家店在刷好评，服务很差。", "retained", {("service", "negative", "服务很差")}),
            ("Selling fake reviews. Contact our agency.", "flagged", set()),
            ("The food was not good and the staff ignored us.", "retained", {("food", "negative", "The food was not good"), ("service", "negative", "the staff ignored us")}),
        ]
        for text, decision, expected in cases:
            with self.subTest(text=text):
                result = assessor(text)
                self.assertEqual(result["decision"], decision)
                actual = {(span["aspect"], span["polarity"], span["quote"]) for span in result["spans"]}
                self.assertTrue(expected <= actual, result)
                self.assertTrue(all(span["quote"] in text for span in result["spans"]))
