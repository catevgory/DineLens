"""Read-only status aggregation for the local control panel."""
from __future__ import annotations

import json
import platform
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ..config import ADMIN_VERSION, ROOT, STARTED_AT, backend_url, db_path
from . import llm, tunnel


def _backend() -> dict:
    try:
        response = requests.get(backend_url() + "/status", timeout=2)
        if response.status_code == 404:
            response = requests.get(backend_url() + "/", timeout=2)
        response.raise_for_status()
        data = response.json()
        return {"online": True, "version": data.get("version", ""),
                "worker_alive": data.get("worker_alive"),
                "queue_size": data.get("queue_size"),
                "jobs": data.get("jobs", {}), "error": None}
    except requests.RequestException as exc:
        return {"online": False, "version": "", "worker_alive": None,
                "queue_size": None, "jobs": {}, "error": str(exc)}


def _local_web() -> dict:
    try:
        started = time.perf_counter()
        response = requests.get(backend_url() + "/web/analysis.html", timeout=2)
        elapsed = time.perf_counter() - started
        return {"online": response.status_code == 200, "status": response.status_code,
                "seconds": round(elapsed, 3), "error": None}
    except requests.RequestException as exc:
        return {"online": False, "status": None, "seconds": None, "error": str(exc)}


def _db() -> dict:
    path = db_path()
    if not path.exists():
        return {"available": False, "path": str(path), "size": 0, "schema": None,
                "counts": {}, "errors": [], "error": None}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            schema = connection.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0]
        except sqlite3.Error:
            schema = None
        rows = connection.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 500").fetchall()
        counts: dict[str, int] = {}
        errors = []
        for row in rows:
            try:
                job = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            state = str(job.get("state") or "unknown")
            counts[state] = counts.get(state, 0) + 1
            if state in {"failed", "needs_operator"} and len(errors) < 20:
                errors.append({
                    "id": job.get("id"),
                    "store": job.get("store"),
                    "state": state,
                    "error": job.get("error"),
                    "finished_at": job.get("finished_at"),
                })
        return {"available": True, "path": str(path), "size": path.stat().st_size,
                "schema": schema, "counts": counts, "errors": errors, "error": None}
    except (sqlite3.Error, OSError) as exc:
        return {"available": False, "path": str(path), "size": 0, "schema": None,
                "counts": {}, "errors": [], "error": str(exc)}


def _git() -> dict:
    try:
        def run(*args):
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=3).stdout.strip()
        return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"),
                "commit": run("rev-parse", "--short", "HEAD"),
                "dirty": bool(run("status", "--porcelain")), "error": None}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"branch": "", "commit": "", "dirty": False, "error": str(exc)}


def _pages() -> dict:
    info = tunnel.deploy()
    url = info.get("pages_url") or ""
    if not url:
        return {"configured": False, "url": "", "online": False, "status": None,
                "seconds": None, "error": None}
    try:
        started = time.perf_counter()
        response = requests.get(url, timeout=3)
        elapsed = time.perf_counter() - started
        return {"configured": True, "url": url, "online": response.status_code == 200,
                "status": response.status_code, "seconds": round(elapsed, 3), "error": None}
    except requests.RequestException as exc:
        return {"configured": True, "url": url, "online": False, "status": None,
                "seconds": None, "error": str(exc)}


def _system() -> dict:
    return {
        "admin_version": ADMIN_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "uptime_seconds": int(time.time() - STARTED_AT),
        "repo": str(ROOT),
    }


def get_status() -> dict:
    jobs = {
        "backend": _backend,
        "local_web": _local_web,
        "db": _db,
        "git": _git,
        "llm": llm.status,
        "tunnel": tunnel.status,
        "pages": _pages,
        "system": _system,
    }
    result = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(function) for name, function in jobs.items()}
        for name, future in futures.items():
            try:
                result[name] = future.result(timeout=8)
            except Exception as exc:
                result[name] = {"error": str(exc)}
    result["backend_url"] = backend_url()
    return result


def job_errors() -> list[dict]:
    return _db().get("errors", [])
