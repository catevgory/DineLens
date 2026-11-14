"""Runtime settings stored in a small JSON file.

Environment variables provide defaults so the service can still be configured
without writing a settings file. The JSON file is the runtime source used by
the control panel.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


DEFAULTS = {
    "llm_enabled": False,
    "llm_model": "qwen2.5:3b",
    "llm_url": "http://127.0.0.1:11434",
}


def _env_defaults() -> dict:
    enabled = os.getenv("DINELENS_LLM_ENABLED", "").strip().lower() == "true"
    if not enabled and os.getenv("DINELENS_ANALYSIS_MODE", "rules").strip().lower() == "ollama":
        enabled = True
    return {
        "llm_enabled": enabled,
        "llm_model": os.getenv("DINELENS_OLLAMA_MODEL", DEFAULTS["llm_model"]).strip() or DEFAULTS["llm_model"],
        "llm_url": os.getenv("DINELENS_OLLAMA_URL", DEFAULTS["llm_url"]).strip() or DEFAULTS["llm_url"],
    }


class Settings:
    """Thread-safe JSON-backed settings with environment defaults."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("DINELENS_SETTINGS_PATH", "var/settings.json"))
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict:
        data = _env_defaults()
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(stored, dict):
            return data
        if "llm_enabled" in stored:
            value = stored["llm_enabled"]
            if isinstance(value, str):
                value = value.strip().lower() == "true"
            data["llm_enabled"] = bool(value)
        for key in ("llm_model", "llm_url"):
            value = stored.get(key)
            if isinstance(value, str) and value.strip():
                data[key] = value.strip()
        return data

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._data)

    @property
    def llm_enabled(self) -> bool:
        with self._lock:
            return bool(self._data.get("llm_enabled"))

    @property
    def llm_model(self) -> str:
        with self._lock:
            return str(self._data.get("llm_model") or DEFAULTS["llm_model"])

    @property
    def llm_url(self) -> str:
        with self._lock:
            return str(self._data.get("llm_url") or DEFAULTS["llm_url"])

    def update(self, *, llm_enabled=None, llm_model=None, llm_url=None) -> dict:
        with self._lock:
            if llm_enabled is not None:
                self._data["llm_enabled"] = bool(llm_enabled)
            if llm_model is not None:
                value = str(llm_model).strip()
                if value:
                    self._data["llm_model"] = value
            if llm_url is not None:
                value = str(llm_url).strip()
                if value:
                    self._data["llm_url"] = value
            self._save()
            return dict(self._data)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
