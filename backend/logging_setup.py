"""Rotating file logging shared by the backend and the local control panel."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CONFIGURED: set[str] = set()


def log_directory() -> Path:
    path = Path(os.getenv("DINELENS_LOG_DIR", "var/logs"))
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(name: str = "backend", level: int = logging.INFO) -> Path:
    """Attach one rotating file handler to the app and uvicorn loggers."""
    target = (log_directory() / f"{name}.log").resolve()
    if str(target) in _CONFIGURED:
        return target
    handler = RotatingFileHandler(
        target, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))

    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == target
               for h in root.handlers):
        root.addHandler(handler)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == target
                   for h in logger.handlers):
            logger.addHandler(handler)
    _CONFIGURED.add(str(target))
    return target
