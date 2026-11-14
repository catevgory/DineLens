"""Local-only DineLens control panel.

Run with:
    .venv\\Scripts\\python.exe -m uvicorn admin.server:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import secrets
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from backend.logging_setup import configure_logging

from .config import ADMIN_VERSION, admin_port, admin_token
from .services import llm, logs, status, tunnel

configure_logging("admin")

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="DineLens Control Panel", version=ADMIN_VERSION)


class SettingsUpdate(BaseModel):
    llm_enabled: StrictBool
    llm_model: str | None = Field(default=None, max_length=200)
    llm_url: str | None = Field(default=None, max_length=500)


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = admin_token()
    if not expected:
        raise HTTPException(503, "DINELENS_ADMIN_TOKEN is not configured.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "Invalid admin token.")
    return True


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def get_status(_=Depends(require_admin)):
    return status.get_status()


@app.get("/api/settings")
def get_settings(_=Depends(require_admin)):
    return llm.get_settings()


@app.put("/api/settings")
def put_settings(payload: SettingsUpdate, _=Depends(require_admin)):
    try:
        return llm.save_settings(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/llm/models")
def get_models(_=Depends(require_admin)):
    current = llm.get_settings()
    return llm.list_models(current.get("llm_url") or "http://127.0.0.1:11434")


@app.post("/api/llm/test")
def test_llm(_=Depends(require_admin)):
    current = llm.get_settings()
    model = current.get("llm_model") or ""
    url = current.get("llm_url") or ""
    if not model or not url:
        raise HTTPException(422, "llm_model and llm_url are required.")
    try:
        return {"enabled": bool(current.get("llm_enabled")), **llm.test(model, url)}
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/logs")
def get_logs(source: str = Query(default="backend"), lines: int = Query(default=300),
             _=Depends(require_admin)):
    try:
        return logs.tail(source, lines)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/job-errors")
def get_job_errors(_=Depends(require_admin)):
    return {"items": status.job_errors()}


@app.post("/api/tunnel/test")
def test_tunnel(_=Depends(require_admin)):
    try:
        return tunnel.test_public()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    uvicorn.run("admin.server:app", host="127.0.0.1", port=admin_port(), reload=False)
