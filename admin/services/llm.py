"""LLM settings and connectivity helpers for the control panel."""
from __future__ import annotations

import time

import requests

from backend.analysis import rule_assessment
from backend.local_model import OllamaAssessor
from backend.settings import Settings

from ..config import admin_token, backend_url, settings_path


def _headers() -> dict:
    return {"X-Admin-Token": admin_token()}


def get_settings() -> dict:
    backend_error = "后端离线"
    try:
        response = requests.get(backend_url() + "/settings", headers=_headers(), timeout=2)
        if response.status_code == 200:
            return {"source": "backend", **response.json()}
        backend_error = ("后端 settings 返回 403（token 不一致）"
                         if response.status_code == 403
                         else "后端 settings 返回 HTTP " + str(response.status_code))
    except requests.RequestException:
        pass
    return {"source": "file", "backend_error": backend_error,
            **Settings(settings_path()).as_dict()}


def save_settings(payload: dict) -> dict:
    try:
        response = requests.put(backend_url() + "/settings", headers=_headers(),
                                json=payload, timeout=3)
    except requests.RequestException as exc:
        raise RuntimeError("后端未运行，暂时不能保存 LLM 设置。") from exc
    if response.status_code != 200:
        detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise RuntimeError(str(detail or ("HTTP " + str(response.status_code))))
    return {"source": "backend", **response.json()}


def list_models(url: str) -> dict:
    try:
        response = requests.get(url.rstrip("/") + "/api/tags", timeout=3)
        response.raise_for_status()
        models = []
        for item in response.json().get("models", []):
            details = item.get("details") or {}
            models.append({
                "name": item.get("name"),
                "size": item.get("size"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
            })
        return {"online": True, "models": models, "error": None}
    except requests.RequestException as exc:
        return {"online": False, "models": [], "error": str(exc)}


def status() -> dict:
    settings = get_settings()
    model = settings.get("llm_model") or ""
    url = settings.get("llm_url") or ""
    listed = list_models(url)
    loaded = []
    if listed["online"]:
        try:
            response = requests.get(url.rstrip("/") + "/api/ps", timeout=3)
            response.raise_for_status()
            loaded = [item.get("name") for item in response.json().get("models", [])]
        except requests.RequestException:
            pass
    return {
        "enabled": bool(settings.get("llm_enabled")),
        "model": model,
        "url": url,
        "source": settings.get("source"),
        "online": listed["online"],
        "model_present": any(item.get("name") == model for item in listed["models"]),
        "loaded": model in loaded,
        "models": listed["models"],
        "error": listed["error"],
    }


def test(model: str, url: str) -> dict:
    sample = "服务一般"
    assessor = OllamaAssessor(model, url, timeout=60)
    rule = rule_assessment(sample)
    started = time.perf_counter()
    result = assessor.screen(sample, rule)
    elapsed = time.perf_counter() - started
    return {
        "sample": sample,
        "decision": result.get("decision"),
        "reasons": result.get("reasons", []),
        "spans": result.get("spans", []),
        "seconds": round(elapsed, 3),
    }
