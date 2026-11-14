"""Reproducible quality and CPU timing evaluation, with explicit data labels."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import time

from .analysis import AnalysisEngine, VERSION, lexicon_sentiment, sentiment


def metrics(truth, predictions, labels):
    per_label = {}
    for label in labels:
        tp = sum(a == b == label for a, b in zip(truth, predictions))
        fp = sum(a != label and b == label for a, b in zip(truth, predictions))
        fn = sum(a == label and b != label for a, b in zip(truth, predictions))
        per_label[label] = {"tp": tp, "fp": fp, "fn": fn,
                            "precision": tp / (tp + fp) if tp + fp else None,
                            "recall": tp / (tp + fn) if tp + fn else None,
                            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}
    f1s = [v["f1"] for v in per_label.values() if v["f1"] is not None]
    return {"count": len(truth), "agreement": sum(a == b for a, b in zip(truth, predictions)) / len(truth) if truth else None,
            "macro_f1": statistics.mean(f1s) if f1s else None, "per_label": per_label,
            "prediction_counts": dict(Counter(predictions))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentiment-tsv", type=Path)
    parser.add_argument("--labelled-reviews", type=Path)
    parser.add_argument("--engine", choices=("rules", "configured"), default="rules")
    parser.add_argument("--sample-per-label", type=int)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--timing-runs", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.sample_per_label is not None and args.sample_per_label <= 0:
        parser.error("--sample-per-label must be positive")
    if args.timing_runs is not None and args.timing_runs < 0:
        parser.error("--timing-runs must be nonnegative")
    from .pipeline import configured_engine
    engine = configured_engine() if args.engine == "configured" else AnalysisEngine()
    timing_runs = args.timing_runs if args.timing_runs is not None else (20 if args.engine == "rules" else 0)
    result = {"pipeline_version": VERSION, "model_version": engine.model_version,
              "analysis_code_sha256": hashlib.sha256(Path(__file__).with_name("analysis.py").read_bytes()).hexdigest(),
              "code_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                              for name in ("analysis.py", "local_model.py", "evaluate.py", "pipeline.py")},
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "environment": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor()},
              "screening": {"status": "awaiting_independent_human_labels", "target_agreement": 0.85}}
    if args.sentiment_tsv:
        sentiment_started = time.perf_counter()
        rows = [line.rsplit("\t", 1) for line in args.sentiment_tsv.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if any(len(r) != 2 or r[1] not in {"0", "1"} for r in rows):
            raise ValueError("TSV must contain text and a 0/1 sentiment label")
        if not rows:
            raise ValueError("TSV must contain labelled sentences")
        indices = list(range(len(rows)))
        if args.sample_per_label is not None:
            rng = random.Random(args.seed)
            indices = sorted(index for label in ("0", "1") for index in
                             rng.sample([i for i, row in enumerate(rows) if row[1] == label], args.sample_per_label))
            rows = [rows[i] for i in indices]
        truth = ["positive" if label == "1" else "negative" for _, label in rows]
        errors = []
        predictions = []
        branch = {"source": "amap", "id": "sentiment-benchmark", "name": "Public benchmark", "city": "Test"}
        for index, (text, _) in zip(indices, rows):
            if args.engine == "rules":
                predictions.append(sentiment(text))
                continue
            try:
                report = engine.analyse([{"source": "amap", "poi_id": branch["id"], "content": text}], branch)
                predictions.append(next(iter(report["sentiment_distribution"]), "screened_out"))
            except (RuntimeError, ValueError) as exc:
                predictions.append("error")
                errors.append({"index": index, "error": str(exc)})
            if len(predictions) % 10 == 0:
                print(json.dumps({"evaluated": len(predictions), "total": len(rows), "errors": len(errors)}), flush=True)
        baseline = [lexicon_sentiment(text) for text, _ in rows]
        result["sentiment"] = {"source": "UCI Sentiment Labelled Sentences Yelp subset", "license": "CC BY 4.0",
            "source_url": "https://archive.ics.uci.edu/dataset/331/sentiment+labelled+sentences",
            "data_sha256": hashlib.sha256(args.sentiment_tsv.read_bytes()).hexdigest(),
            "selection": {"sample_per_label": args.sample_per_label, "seed": args.seed, "indices": indices},
            "engine": metrics(truth, predictions, ["positive", "negative"]),
            "vader_baseline": metrics(truth, [sentiment(text) for text, _ in rows], ["positive", "negative"]),
            "lexicon_baseline": metrics(truth, baseline, ["positive", "negative"]),
            "coverage": sum(p in {"positive", "negative"} for p in predictions) / len(rows),
            "errors": errors,
            "seconds": time.perf_counter() - sentiment_started,
            "error_examples": [{"text": row[0], "label": actual, "predicted": predicted}
                               for row, actual, predicted in zip(rows, truth, predictions) if actual != predicted][:5]}
    if args.labelled_reviews:
        rows = [json.loads(line) for line in args.labelled_reviews.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        truth = [r["screening_label"] for r in rows]
        if any(label not in {"retained", "flagged"} for label in truth):
            raise ValueError("screening_label must be retained or flagged")
        from .analysis import normalized_text
        predicted = [engine.assessor(normalized_text(r["review"].get("content")))["decision"] for r in rows]
        result["screening"] = {"status": "measured", "target_agreement": 0.85,
                                "data_sha256": hashlib.sha256(args.labelled_reviews.read_bytes()).hexdigest(),
                                **metrics(truth, predicted, ["retained", "flagged"])}
    branch = {"source": "amap", "id": "benchmark", "name": "Generated CPU benchmark", "city": "Test"}
    generated = [{"source": "amap", "poi_id": "benchmark", "review_id": str(i),
                  "content": "食物很好，但服务很慢。The atmosphere was lovely.", "rating": str(i % 5 + 1)} for i in range(200)]
    durations = []
    if timing_runs:
        engine.analyse(generated, branch)
    for _ in range(timing_runs):
        start = time.perf_counter()
        engine.analyse(generated, branch)
        durations.append(time.perf_counter() - start)
    result["performance"] = {"scope": "analysis_only_generated_fixture", "reviews_per_run": 200,
                             "runs": timing_runs, "p50_seconds": statistics.median(durations) if durations else None,
                             "p95_seconds": sorted(durations)[math.ceil(len(durations) * .95) - 1] if durations else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sentiment": result.get("sentiment", {}).get("engine"), "performance": result["performance"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
