"""Paths and environment for the local DineLens control panel."""
from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_VERSION = "1.0.0"
STARTED_AT = time.time()


def backend_url() -> str:
    return os.getenv("DINELENS_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def admin_token() -> str:
    return os.getenv("DINELENS_ADMIN_TOKEN", "").strip()


def admin_port() -> int:
    return int(os.getenv("DINELENS_ADMIN_PORT", "8765"))


def db_path() -> Path:
    raw = os.getenv("DINELENS_DB_PATH", "var/dinelens.sqlite3")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def log_dir() -> Path:
    raw = os.getenv("DINELENS_LOG_DIR", "var/logs")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def settings_path() -> Path:
    raw = os.getenv("DINELENS_SETTINGS_PATH", "var/settings.json")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def deploy_file() -> Path:
    return ROOT / "tools" / "deploy.json"
