"""Safe tail helpers for the local control panel."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import ROOT, log_dir

MAX_LINES = 2000
MAX_BYTES = 2_000_000


def log_files() -> dict[str, Path]:
    return {
        "backend": log_dir() / "backend.log",
        "admin": log_dir() / "admin.log",
        "tunnel": ROOT / "tools" / "tunnel.log",
    }


def tail(source: str, lines: int = 300) -> dict:
    files = log_files()
    if source not in files:
        raise ValueError("Unknown log source")
    lines = max(1, min(MAX_LINES, int(lines)))
    path = files[source]
    relative = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    if not path.exists():
        return {"source": source, "path": relative, "available": False,
                "size": 0, "modified": None, "lines": []}
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - MAX_BYTES))
        data = handle.read().decode("utf-8", errors="replace")
    if size > MAX_BYTES and "\n" in data:
        data = data.split("\n", 1)[1]
    content = data.splitlines()[-lines:]
    return {
        "source": source,
        "path": relative,
        "available": True,
        "size": size,
        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "lines": content,
    }
