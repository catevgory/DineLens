"""FastAPI adapter for the minimal DineLens job/cancel skeleton.

Run from the repository root:

    python -m uvicorn backend.app:app --reload --port 8000

The Cloudflare Pages Function strips the ``/api`` prefix before proxying, so
this app exposes ``/jobs`` and the browser calls ``/api/jobs``.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .job_manager import JobManager


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


MANAGER = JobManager(
    collect_steps=_int_env("DINELENS_COLLECT_STEPS", 5),
    analyse_steps=_int_env("DINELENS_ANALYSE_STEPS", 5),
    step_delay=_float_env("DINELENS_STEP_DELAY", 0.4),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    MANAGER.start_worker()
    try:
        yield
    finally:
        MANAGER.shutdown()


app = FastAPI(
    title="DineLens jobs (minimal)",
    version="0.1.0",
    description=(
        "Single-worker job skeleton with cooperative cancellation in queued, "
        "collecting and analysing. Collection and analysis are simulated."
    ),
    lifespan=lifespan,
)

# The demo page can be opened from disk during local development. On
# Cloudflare Pages the browser and the API share an origin, so this is only a
# development convenience. Add authentication before exposing the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobCreate(BaseModel):
    store: str = Field(min_length=1, max_length=200)


class JobView(BaseModel):
    id: str
    store: str
    state: str
    cancel_requested: bool
    progress: str
    error: str | None
    report: dict | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@app.get("/")
def health() -> dict:
    return {"service": "dinelens-jobs", "status": "ok"}


@app.post("/jobs", response_model=JobView, status_code=201)
def create_job(payload: JobCreate) -> dict:
    try:
        return MANAGER.create_job(payload.store)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/jobs", response_model=list[JobView])
def list_jobs() -> list[dict]:
    return MANAGER.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str) -> dict:
    job = MANAGER.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/jobs/{job_id}/cancel", response_model=JobView)
def cancel_job(job_id: str) -> dict:
    result = MANAGER.request_cancel(job_id)
    if not result["ok"]:
        if result["reason"] == "not_found":
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(status_code=409, detail=result["reason"])
    return result["job"]
