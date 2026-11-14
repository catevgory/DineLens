"""DineLens API. Pages strips /api, so backend paths stay unprefixed."""
from contextlib import asynccontextmanager
from collections import defaultdict, deque
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Literal
from urllib.parse import urljoin

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from .job_manager import JobManager, NeedsOperator
from .logging_setup import configure_logging
from .pipeline import Collector, configured_analyser, search_branches
from .settings import Settings
from .storage import Storage, iso, token_hash

configure_logging("backend")

COOKIE = "dinelens_owner"


class JobCreate(BaseModel):
    store: str = Field(min_length=1, max_length=200)
    branch_id: str | None = Field(default=None, max_length=128)
    branch_confirmed: StrictBool = False


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
    branch: dict | None = None
    report_id: str | None = None


class SettingsUpdate(BaseModel):
    llm_enabled: StrictBool
    llm_model: str | None = Field(default=None, max_length=200)
    llm_url: str | None = Field(default=None, max_length=500)


def create_app(*, database_path=None, collector=None, engine=None, searcher=None, origins=None):
    allowed = origins or os.getenv("DINELENS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    allowed = [value.strip() for value in allowed if value.strip()]
    secure_default = "true" if any(value.startswith("https://") for value in allowed) else "false"
    secure_cookie = os.getenv("DINELENS_COOKIE_SECURE", secure_default).lower() == "true"
    session_requests = defaultdict(deque)
    session_lock = threading.Lock()
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app):
        storage = Storage(database_path or os.getenv("DINELENS_DB_PATH", "var/dinelens.sqlite3"),
                          share_days=int(os.getenv("DINELENS_SHARE_DAYS", "30")),
                          raw_days=int(os.getenv("DINELENS_RAW_DAYS", "90")),
                          report_days=int(os.getenv("DINELENS_REPORT_DAYS", "365")))
        analyser = engine.analyse if engine is not None else configured_analyser(settings)
        manager = JobManager(storage=storage, collector=collector or Collector(), analyser=analyser)
        app.state.storage, app.state.manager = storage, manager
        manager.start_worker()
        try:
            yield
        finally:
            manager.shutdown(timeout=3)

    app = FastAPI(title="DineLens", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE", "PUT"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Authorization", "X-Admin-Token"])

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def owner(request: Request):
        current = request.app.state.storage.owner(request.cookies.get(COOKIE))
        if not current:
            raise HTTPException(401, "请先建立匿名会话。")
        return current["id"]

    def check_origin(request):
        if request.headers.get("origin") not in allowed:
            raise HTTPException(403, "Origin is not allowed")

    def writer(request: Request, owner_id=Depends(owner), x_csrf_token: str | None = Header(default=None)):
        check_origin(request)
        expected = token_hash("csrf:" + request.cookies[COOKIE])
        if not secrets.compare_digest(x_csrf_token or "", expected):
            raise HTTPException(403, "Invalid CSRF token")
        return owner_id

    def found(value):
        if value is None:
            raise HTTPException(404, "Resource is not available")
        return value

    def admin(x_admin_token: str | None = Header(default=None)):
        expected = os.getenv("DINELENS_ADMIN_TOKEN", "").strip()
        if not expected:
            raise HTTPException(403, "Admin token is not configured.")
        if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
            raise HTTPException(403, "Invalid admin token.")
        return True

    @app.get("/")
    def health():
        return {"service": "dinelens-jobs", "status": "ok", "version": "1.0.0"}

    @app.get("/status")
    def status(request: Request):
        manager = getattr(request.app.state, "manager", None)
        if manager is None:
            raise HTTPException(503, "Worker is not ready")
        return {"service": "dinelens-jobs", "status": "ok", "version": "1.0.0",
                **manager.status()}

    @app.get("/settings")
    def get_settings(request: Request, _=Depends(admin)):
        return request.app.state.settings.as_dict()

    @app.put("/settings")
    def put_settings(payload: SettingsUpdate, request: Request, _=Depends(admin)):
        if payload.llm_url is not None and not payload.llm_url.startswith(("http://", "https://")):
            raise HTTPException(422, "llm_url must start with http:// or https://")
        return request.app.state.settings.update(
            llm_enabled=payload.llm_enabled,
            llm_model=payload.llm_model,
            llm_url=payload.llm_url,
        )

    @app.post("/owner-session")
    def create_session(request: Request, response: Response):
        check_origin(request)
        storage = request.app.state.storage
        token = request.cookies.get(COOKIE)
        current = storage.owner(token)
        if not current:
            with session_lock:
                now = time.monotonic()
                ip = request.client.host if request.client else "unknown"
                times = session_requests[ip]
                while times and times[0] <= now - 60:
                    times.popleft()
                if len(times) >= 30:
                    raise HTTPException(429, "Too many session requests", headers={"Retry-After": "60"})
                times.append(now)
                if len(session_requests) > 2000:
                    session_requests.clear()
            token, _, expiry = storage.create_owner()
            response.status_code = 201
            response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie, samesite="lax", max_age=365 * 86400)
        else:
            expiry = iso(current["expires_at"])
        return {"csrf_token": token_hash("csrf:" + token), "expires_at": expiry}

    @app.get("/owner-session")
    def get_session(request: Request, owner_id=Depends(owner)):
        current = request.app.state.storage.owner(request.cookies[COOKIE])
        return {"csrf_token": token_hash("csrf:" + request.cookies[COOKIE]), "expires_at": iso(current["expires_at"])}

    @app.get("/branches")
    def branches(request: Request, q: str = Query(min_length=1, max_length=200),
                 city: str = Query(min_length=1, max_length=100), source: Literal["amap", "baidu"] = "amap",
                 city_code: str | None = Query(default=None, pattern=r"^\d{1,8}$")):
        if not q.strip() or not city.strip():
            raise HTTPException(422, "餐厅名称和城市不能为空。")
        try:
            rows = (searcher or search_branches)(q.strip(), city.strip(), source, city_code=city_code)
            results = []
            for row in rows[:20]:
                if row.get("id"):
                    branch = {k: row.get(k) for k in ("id", "name", "address", "city", "x", "y")}
                    branch.update(source=source, city=row.get("city") or city.strip())
                    results.append(request.app.state.storage.remember_branch(branch))
            return {"items": results}
        except NeedsOperator as exc:
            raise HTTPException(422, str(exc)) from exc
        except (Exception, SystemExit) as exc:
            raise HTTPException(503, "餐厅来源暂不可用，请检查采集配置或稍后重试。") from exc

    @app.post("/jobs", response_model=JobView, status_code=201)
    def create_job(payload: JobCreate, request: Request, response: Response, owner_id=Depends(writer),
                   idempotency_key: str | None = Header(default=None, min_length=1, max_length=128)):
        branch = None
        if payload.branch_id:
            if not payload.branch_confirmed:
                raise HTTPException(422, "请确认目标分店。")
            branch = found(request.app.state.storage.branch(payload.branch_id))
        try:
            value = request.app.state.manager.create_job(payload.store, owner_id=owner_id, branch=branch, idempotency_key=idempotency_key)
            response.headers["Location"] = "jobs/" + value["id"]
            return value
        except ValueError as exc:
            raise HTTPException(409 if idempotency_key else 422, str(exc)) from exc

    @app.get("/jobs", response_model=list[JobView])
    def list_jobs(request: Request, owner_id=Depends(owner)):
        return request.app.state.manager.list_jobs(owner_id)

    @app.get("/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str, request: Request, response: Response, owner_id=Depends(owner)):
        response.headers["Retry-After"] = "2"
        return found(request.app.state.manager.get_job(job_id, owner_id))

    @app.post("/jobs/{job_id}/cancel", response_model=JobView)
    def cancel_job(job_id: str, request: Request, owner_id=Depends(writer)):
        result = request.app.state.manager.request_cancel(job_id, owner_id)
        if not result["ok"]:
            raise HTTPException(404 if result["reason"] == "not_found" else 409, result["reason"])
        return result["job"]

    @app.get("/reports/{report_id}")
    def report(report_id: str, request: Request, owner_id=Depends(owner)):
        return found(request.app.state.storage.report(report_id, owner_id))

    @app.get("/history")
    def history(request: Request, owner_id=Depends(owner), limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=2147483647)):
        return request.app.state.storage.history(owner_id, limit, offset)

    @app.get("/notifications")
    def notifications(request: Request, owner_id=Depends(owner), limit: int = Query(default=20, ge=1, le=100),
                      offset: int = Query(default=0, ge=0, le=2147483647)):
        return request.app.state.storage.notifications(owner_id, limit, offset)

    @app.post("/notifications/{notification_id}/read", status_code=204)
    def read_notification(notification_id: str, request: Request, owner_id=Depends(writer)):
        if not request.app.state.storage.read_notification(notification_id, owner_id):
            raise HTTPException(404, "Resource is not available")

    @app.delete("/history/{history_id}", status_code=204)
    def delete_history(history_id: str, request: Request, owner_id=Depends(writer)):
        if not request.app.state.storage.delete_history(history_id, owner_id):
            raise HTTPException(404, "Resource is not available")

    @app.post("/reports/{report_id}/shares", status_code=201)
    def create_share(report_id: str, request: Request, owner_id=Depends(writer)):
        share = found(request.app.state.storage.create_share(report_id, owner_id))
        origin = request.headers["origin"]
        local_web = origin + "/web/" if origin == str(request.base_url).rstrip("/") else origin + "/"
        web_url = os.getenv("DINELENS_WEB_URL", local_web)
        share["share_url"] = urljoin(web_url.rstrip("/") + "/", "report.html") + "#share=" + share.pop("token")
        return share

    @app.get("/reports/{report_id}/shares")
    def shares(report_id: str, request: Request, owner_id=Depends(owner)):
        return {"items": found(request.app.state.storage.shares(report_id, owner_id))}

    @app.delete("/shares/{share_id}", status_code=204)
    def revoke(share_id: str, request: Request, owner_id=Depends(writer)):
        if not request.app.state.storage.revoke_share(share_id, owner_id):
            raise HTTPException(404, "Resource is not available")

    @app.get("/shared-report")
    def shared(request: Request, authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Share token required")
        return found(request.app.state.storage.shared_report(authorization[7:]))

    app.mount("/web", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="web")
    return app


app = create_app()
