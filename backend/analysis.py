"""Review analysis accepting the existing fetchers' normalized dictionaries."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import statistics
import time
import unicodedata
from functools import lru_cache
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

VERSION = "analysis-1.0.0"
ASPECTS = {
    "food": ("food", "meal", "pizza", "dish", "steak", "burger", "soup", "食物", "味道", "菜", "饭"),
    "service": ("service", "staff", "waiter", "server", "服务", "店员", "等待"),
    "price": ("price", "value", "expensive", "cheap", "价格", "性价比", "便宜", "贵"),
    "atmosphere": ("atmosphere", "ambience", "decor", "noisy", "quiet", "环境", "氛围", "安静", "吵"),
}
POSITIVE = ("很好", "好吃", "美味", "新鲜", "友好", "热情", "干净", "舒适", "便宜", "实惠", "安静", "不错", "满意", "很棒", "好", "great", "good", "delicious", "fresh", "friendly", "clean", "comfortable", "affordable", "cheap", "excellent", "amazing", "tasty", "lovely", "love")
NEGATIVE = ("难吃", "很差", "糟糕", "失望", "冷", "慢", "脏", "贵", "吵", "差", "凉的", "凉了", "菜凉", "等了", "等太久", "等很久", "不会再来", "再也不来", "不想再来", "bad", "awful", "terrible", "disappointing", "slow", "rude", "dirty", "expensive", "overpriced", "noisy", "cold", "bland", "stale", "hate")
BREAKS = re.compile(r"[.!?。！？,，;；\n]+|\b(?:but|however|and)\b|但是|不过|但", re.I)
PROMOTION = re.compile(
    r"^(?:selling|sell|we sell|we offer|buy)\s+(?:fake\s+|positive\s+|5[- ]star\s+)?reviews?\b.{0,200}(?:contact|whatsapp|telegram|dm us)"
    r"|^(?:刷好评|出售好评|有偿好评|五星好评返现).{0,100}(?:微信|联系|咨询|下单)", re.I)
# A review-manipulation term plus a contact/offer term in the same sentence.
# This is deliberately only a *route* to the LLM, not an automatic flag:
# legitimate complaints that mention fake reviews should still be retained.
PROMOTION_ROUTE = re.compile(
    r"(?:写|代写|刷|买卖|招)[^。！？\n]{0,12}(?:好评|评论|评价)[^。！？\n]{0,80}"
    r"(?:微信|加v|返现|联系|私聊|下单|兼职|招聘|whatsapp|telegram|dm|contact)"
    r"|(?:微信|加v|返现|联系|私聊|下单|兼职|招聘|whatsapp|telegram|dm|contact)"
    r"[^。！？\n]{0,80}(?:写|代写|刷|买卖|招)[^。！？\n]{0,12}(?:好评|评论|评价)"
    r"|(?:write|post|sell|buy|paid)[^.!?\n]{0,20}reviews?[^.!?\n]{0,80}"
    r"(?:contact|whatsapp|telegram|dm|wechat)",
    re.I,
)


def normalized_text(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip()


def pattern(word):
    return r"\b" + re.escape(word) + r"\b" if word.isascii() else re.escape(word)


def lexicon_sentiment(text):
    """Bilingual, deterministic lexicon baseline with local negation."""
    votes, occupied = [], set()
    for words, sign in ((POSITIVE, 1), (NEGATIVE, -1)):
        for word in sorted(words, key=len, reverse=True):
            for match in re.finditer(pattern(word), text, re.I):
                covered = set(range(match.start(), match.end()))
                if covered & occupied:
                    continue
                occupied.update(covered)
                prefix = text[max(0, match.start() - 14):match.start()].lower()
                negate = re.search(r"(?:不|没|并非|not\s+|never\s+|n't\s+)(?:太|很|特别|really\s+|very\s+)?$", prefix)
                votes.append(-sign if negate else sign)
    if 1 in votes and -1 in votes:
        return "mixed"
    if votes:
        return "positive" if sum(votes) > 0 else "negative"
    return "unknown"


@lru_cache(maxsize=1)
def english_analyser():
    return SentimentIntensityAnalyzer()


def sentiment(text):
    if re.search(r"[\u3400-\u9fff]", text):
        return lexicon_sentiment(text)
    compound = english_analyser().polarity_scores(text)["compound"]
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return lexicon_sentiment(text)


def rule_assessment(text):
    spans, start = [], 0
    segments = []
    for match in BREAKS.finditer(text):
        segments.append((start, match.start()))
        start = match.end()
    segments.append((start, len(text)))
    for left, right in segments:
        clause = text[left:right].strip()
        if not clause:
            continue
        left += text[left:right].find(clause)
        found = []
        for aspect, words in ASPECTS.items():
            positions = [m.start() for w in words for m in re.finditer(pattern(w), clause, re.I)]
            if positions:
                found.append((min(positions), aspect))
        found = sorted(found) or [(0, "other")]
        for index, (position, aspect) in enumerate(found):
            a = 0 if index == 0 else position
            b = found[index + 1][0] if index + 1 < len(found) else len(clause)
            quote = clause[a:b].strip()
            if quote:
                offset = clause.find(quote, a)
                spans.append({"aspect": aspect, "polarity": sentiment(quote), "quote": quote,
                              "start": left + offset, "end": left + offset + len(quote)})
    flagged = bool(PROMOTION.search(text))
    if flagged:
        return {"decision": "flagged", "reasons": ["PROMOTIONAL_REVIEW_OFFER"], "spans": spans}
    no_clear = bool(text and spans and not any(s["polarity"] in {"positive", "negative", "neutral"} for s in spans))
    if PROMOTION_ROUTE.search(text):
        reasons = ["SUSPICIOUS_PROMOTION"]
        if no_clear:
            reasons.append("NO_CLEAR_SENTIMENT")
        return {"decision": "uncertain", "reasons": reasons, "spans": spans}
    if no_clear:
        return {"decision": "uncertain", "reasons": ["NO_CLEAR_SENTIMENT"], "spans": spans}
    return {"decision": "retained", "reasons": [], "spans": spans}


def parse_date(value):
    if not value:
        return None
    try:
        if str(value).replace(".", "", 1).isdigit() and len(str(value)) >= 10:
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def rating_value(row):
    value = row.get("rating")
    if value in (None, "") or isinstance(value, bool):
        return None
    known_scale = row.get("source") in {"amap", "baidu"} or (row.get("rating_min"), row.get("rating_max")) == (1, 5)
    if not known_scale:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and 1 <= number <= 5 else None
    except (TypeError, ValueError):
        return None


def review_identity(row):
    raw = row.get("raw", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    external = row.get("review_id") or row.get("source_review_id")
    if isinstance(raw, dict):
        external = external or next((raw[k] for k in ("review_id", "reviewId", "comment_id", "commentId", "id") if raw.get(k)), None)
    fallback = (normalized_text(row.get("content")), str(row.get("author") or ""), str(row.get("date") or ""))
    identity = (row.get("source"), row.get("poi_id"), str(external) if external else fallback)
    return "rev_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]


def calculate_score(rows, *, method="bayesian_time_v1", now=None, prior_mean=3.5, prior_strength=10, half_life_days=180):
    if method not in {"mean_v1", "bayesian_v1", "bayesian_time_v1"}:
        raise ValueError("Unknown scoring method")
    if not all(math.isfinite(v) for v in (prior_mean, prior_strength, half_life_days)) or not 1 <= prior_mean <= 5 or prior_strength <= 0 or half_life_days <= 0:
        raise ValueError("Invalid scoring parameters")
    now = now or datetime.now(timezone.utc)
    values, weights, missing_dates = [], [], 0
    for row in rows:
        value = rating_value(row)
        if value is None:
            continue
        date = parse_date(row.get("date"))
        age = max(0, (now - date).total_seconds() / 86400) if date else 0
        weight = max(1e-12, 2 ** (-age / half_life_days)) if method == "bayesian_time_v1" else 1
        missing_dates += date is None
        values.append(value)
        weights.append(weight)
    count = len(values)
    score = None
    if count:
        score = statistics.mean(values) if method == "mean_v1" else (
            sum(v * w for v, w in zip(values, weights)) + prior_strength * prior_mean
        ) / (sum(weights) + prior_strength)
    return {"value": score, "valid_count": count, "sample_status": "none" if not count else ("low" if count < 5 else "adequate"),
            "method": method, "scale_min": 1, "scale_max": 5,
            "raw_mean": statistics.mean(values) if values else None,
            "effective_count": sum(weights), "missing_dates": missing_dates,
            "parameters": {"prior_mean": prior_mean, "prior_strength": prior_strength, "half_life_days": half_life_days}}


def wilson_interval(negative, total, z=1.96):
    if not total:
        return None
    p = negative / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"lower": max(0, center - margin), "upper": min(1, center + margin), "confidence": 0.95,
            "negative_count": negative, "classified_count": total}


class AnalysisEngine:
    def __init__(self, assessor=None, model_version="vader3.3.2_zh_domain_v1", score_method="bayesian_time_v1",
                 screening_mode="rules"):
        if score_method not in {"mean_v1", "bayesian_v1", "bayesian_time_v1"}:
            raise ValueError("Unknown scoring method")
        self.assessor = assessor or rule_assessment
        self.model_version = model_version
        self.score_method = score_method
        self.screening_mode = screening_mode

    def analyse(self, rows, branch, cancel_check=lambda: None, progress=lambda stage: None, now=None):
        now = now or datetime.now(timezone.utc)
        started = time.perf_counter()
        unique, duplicates, rejected, assessments, retained, uncertain = {}, [], [], [], [], []
        evidence, candidates, aspect_counts = {}, defaultdict(list), defaultdict(Counter)
        reasons = Counter()
        for row in rows:
            cancel_check()
            if str(row.get("poi_id") or "") != str(branch["id"]) or row.get("source") != branch["source"]:
                rejected.append({"reason": "BRANCH_OR_SOURCE_MISMATCH"})
                continue
            rid = review_identity(row)
            if rid in unique:
                duplicates.append(rid)
            else:
                unique[rid] = row
        progress("screening")
        sentiments = Counter()
        for rid, row in unique.items():
            cancel_check()
            text = normalized_text(row.get("content"))
            assessed = self.assessor(text) if text else {"decision": "retained", "reasons": ["NO_TEXT"], "spans": []}
            if assessed["decision"] not in {"retained", "flagged", "uncertain"}:
                raise ValueError("Invalid screening decision")
            if assessed["decision"] == "flagged" and not assessed["reasons"]:
                raise ValueError("Flagged reviews require a reason")
            assessed_reasons = list(assessed.get("reasons") or [])
            if assessed["decision"] == "uncertain" and not assessed_reasons:
                assessed_reasons = ["UNCERTAIN"]
            eligible = assessed["decision"] == "retained" and rating_value(row) is not None
            if eligible:
                rating_reason = None
            elif assessed["decision"] == "flagged":
                rating_reason = "SCREENED_OUT"
            elif assessed["decision"] == "uncertain":
                rating_reason = "SCREENING_UNCERTAIN"
            else:
                rating_reason = "NO_VALID_NUMERIC_RATING"
            assessment = {"review_id": rid, "decision": assessed["decision"], "reasons": assessed_reasons, "aspects": [],
                          "rating_eligible": eligible, "rating_reason": rating_reason,
                          "method": assessed.get("method", "rules")}
            reasons.update(assessed_reasons)
            assessments.append(assessment)
            if assessed["decision"] == "uncertain":
                uncertain.append(rid)
                continue
            if assessed["decision"] == "flagged":
                continue
            retained.append(row)
            labels = defaultdict(set)
            for span in assessed["spans"]:
                quote = span["quote"]
                if not quote or quote not in text or span["aspect"] not in {*ASPECTS, "other"} or span["polarity"] not in {"positive", "negative", "neutral", "mixed", "unknown"}:
                    raise ValueError("Analysis evidence is invalid")
                offset = text.index(quote)
                item = {"review_id": rid, "aspect": span["aspect"], "polarity": span["polarity"], "quote": quote,
                        "start": offset, "end": offset + len(quote), "evidence_status": "available"}
                assessment["aspects"].append({k: item[k] for k in ("aspect", "polarity", "start", "end")})
                candidates[span["aspect"]].append(item)
                labels[span["aspect"]].add(span["polarity"])
            for aspect, polarities in labels.items():
                overall = "mixed" if "mixed" in polarities or {"positive", "negative"} <= polarities else next((p for p in ("positive", "negative", "neutral", "unknown") if p in polarities), "unknown")
                aspect_counts[aspect][overall] += 1
            all_labels = set().union(*labels.values()) if labels else set()
            overall = "mixed" if "mixed" in all_labels or {"positive", "negative"} <= all_labels else next((p for p in ("positive", "negative", "neutral") if p in all_labels), "unknown")
            sentiments[overall] += 1
            if text:
                evidence[rid] = {"text": text, "url": str(row.get("url") or "")}
        progress("summary")
        cancel_check()
        summary = []
        for aspect, items in sorted(candidates.items()):
            seen = set()
            for polarity in ("positive", "negative", "mixed", "neutral", "unknown"):
                choices = sorted((x for x in items if x["polarity"] == polarity), key=lambda x: (len(x["quote"]), x["review_id"]))
                for item in choices:
                    if item["quote"].casefold() not in seen:
                        seen.add(item["quote"].casefold())
                        summary.append(item)
                        break
                if len(seen) == 2:
                    break
        progress("scoring")
        score = calculate_score(retained, method=self.score_method, now=now)
        dates = [d for r in unique.values() if (d := parse_date(r.get("date")))]
        warnings = []
        if score["sample_status"] == "none":
            warnings.append("NO_NUMERIC_RATINGS")
        elif score["sample_status"] == "low":
            warnings.append("LOW_RATING_SAMPLE")
        if not evidence:
            warnings.append("NO_USABLE_TEXT")
        if not evidence and not score["valid_count"]:
            warnings.append("NO_USABLE_EVIDENCE")
        if rejected:
            warnings.append("REJECTED_SOURCE_RECORDS")
        if uncertain:
            warnings.append("UNCERTAIN_REVIEWS_EXCLUDED")
            if any("LLM_DISABLED" in a["reasons"] for a in assessments):
                warnings.append("LLM_DISABLED")
            if any("LLM_UNAVAILABLE" in a["reasons"] for a in assessments):
                warnings.append("LLM_UNAVAILABLE")
        cancel_check()
        dataset = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {"branch": branch, "counts": {"received": len(rows), "unique": len(unique), "duplicates": len(duplicates), "rejected": len(rejected),
                "retained": len(retained), "flagged": len(unique) - len(retained) - len(uncertain), "uncertain": len(uncertain),
                "text_eligible": len(evidence), "rating_eligible": score["valid_count"]},
                "score": score, "aspects": [{"aspect": a, **{p: c[p] for p in ("positive", "negative", "neutral", "mixed", "unknown")}} for a, c in sorted(aspect_counts.items())],
                "summary": summary, "screening": {"reason_counts": dict(reasons), "assessments": assessments,
                "uncertain": len(uncertain),
                "duplicate_records": [{"review_id": rid, "reason": "DUPLICATE_SOURCE_ID_OR_CONTENT_AUTHOR_DATE"} for rid in duplicates],
                "rejected_records": rejected}, "sentiment_distribution": dict(sentiments),
                "negative_rate_interval": wilson_interval(sentiments["negative"], sentiments["positive"] + sentiments["negative"]),
                "rating_distribution": dict(Counter(format(v, "g") for r in retained if (v := rating_value(r)) is not None)),
                "evidence": evidence, "warnings": warnings,
                "review_period": {"start": min(dates).isoformat() if dates else None, "end": max(dates).isoformat() if dates else None},
                "versions": {"pipeline": VERSION, "model": self.model_version, "dataset": dataset,
                             "config": "score_defaults_v1", "screening": self.screening_mode},
                "analysis_seconds": time.perf_counter() - started}
