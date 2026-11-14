"""Screening policy: rules first, optional LLM only for uncertain reviews."""
from __future__ import annotations

from .analysis import rule_assessment


class HybridAssessor:
    """Keep deterministic rules in charge and route only uncertain cases to an LLM.

    The LLM is optional. When it is disabled, or when a call fails, uncertain
    reviews are returned as ``uncertain`` and are excluded by the analysis
    engine. A failed call opens a circuit for the rest of the job so a dead
    model does not add one timeout per review.
    """

    def __init__(self, llm=None, *, enabled: bool = False):
        self.llm = llm
        self.enabled = bool(enabled and llm is not None)
        self._unavailable = False

    def __call__(self, text):
        rule = rule_assessment(text)
        rule["method"] = "rules"
        if rule["decision"] != "uncertain":
            return rule

        if not self.enabled:
            return self._fallback(rule, "LLM_DISABLED")

        if self._unavailable:
            return self._fallback(rule, "LLM_UNAVAILABLE")

        try:
            result = self.llm.screen(text, rule)
            if result.get("decision") not in {"retained", "flagged"}:
                raise ValueError("Invalid model screening decision")
            if result["decision"] == "flagged" and not result.get("reasons"):
                result["reasons"] = ["MODEL_FLAGGED"]
            result["method"] = "llm"
            return result
        except Exception:
            self._unavailable = True
            return self._fallback(rule, "LLM_UNAVAILABLE")

    @staticmethod
    def _fallback(rule, reason):
        reasons = list(rule.get("reasons") or [])
        if reason not in reasons:
            reasons.append(reason)
        return {
            "decision": "uncertain",
            "reasons": reasons,
            "spans": list(rule.get("spans") or []),
            "method": "rules_fallback",
        }
