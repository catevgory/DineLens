"""Operational CLI and JSONL entry point for the analysis engine."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import configured_engine
from .storage import Storage, WorkerLock, encode


def main(argv=None):
    parser = argparse.ArgumentParser(description="DineLens analysis and database maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("cleanup")
    retry = sub.add_parser("retry")
    retry.add_argument("job_id")
    retry.add_argument("--reason", required=True)
    analysis = sub.add_parser("analyse")
    analysis.add_argument("input", type=Path)
    analysis.add_argument("--source", choices=["amap", "baidu"], required=True)
    analysis.add_argument("--poi-id", required=True)
    analysis.add_argument("--store", required=True)
    analysis.add_argument("--city", required=True)
    analysis.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "analyse":
        rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        branch = {"source": args.source, "id": args.poi_id, "name": args.store, "city": args.city}
        report = configured_engine().analyse(rows, branch)
        report["source_mode"] = "file"
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(encode({"output": str(args.output), "counts": report["counts"], "score": report["score"]}))
        return
    storage = Storage(os.getenv("DINELENS_DB_PATH", "var/dinelens.sqlite3"),
                      raw_days=int(os.getenv("DINELENS_RAW_DAYS", "90")),
                      report_days=int(os.getenv("DINELENS_REPORT_DAYS", "365")),
                      share_days=int(os.getenv("DINELENS_SHARE_DAYS", "30")))
    if args.command == "cleanup":
        print(encode(storage.cleanup()))
    elif args.command == "retry":
        lock = WorkerLock(storage.path)
        lock.acquire()
        try:
            job = next((j for j in storage.load_jobs() if j["id"] == args.job_id), None)
            if not job or job["state"] not in {"failed", "needs_operator"}:
                raise ValueError("Only failed or needs_operator jobs can be retried")
            job.update(state="queued", progress="queued for retry", error=None, finished_at=None,
                       started_at=None, cancel_requested=False, operator_note=args.reason)
            storage.save_job(job)
            print(encode({"job_id": job["id"], "state": "queued", "reason": args.reason}))
        finally:
            lock.release()
    else:
        with storage.connection() as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0]
        print(encode({"schema_version": version, "database": str(storage.path)}))


if __name__ == "__main__":
    main()
