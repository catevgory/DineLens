# DineLens Control Panel (Phase 1)

Local-only dashboard for the backend, local web UI, Cloudflare Tunnel, Pages, LLM and logs.

It is intentionally **not** mounted on the public backend. It listens on
`127.0.0.1:8765` only. Tunnel start/stop and backend process control are not part
of Phase 1.

## Start

Create `var/admin_env.bat` (ignored by Git) if you want to keep the token across launches:

```bat
set DINELENS_ADMIN_TOKEN=replace-with-a-long-random-token
```

Then double-click `start_all.bat`. It:

1. starts the backend on `127.0.0.1:8000` if that port is free;
2. starts this panel on `127.0.0.1:8765` if that port is free;
3. opens Edge in `--app` mode (fallback: default browser).

If `var/admin_env.bat` does not exist, the launcher asks for the token once.
The browser asks for the same token on first load and keeps it in `sessionStorage`
only.

If port 8000 is already occupied by a backend started before this control panel
was added, `start_all.bat` will not restart it. Close that old process and run
the launcher again to enable `/status`, worker state and file logging.

Manual start:

```powershell
.venv\Scripts\python.exe -m uvicorn admin.server:app --host 127.0.0.1 --port 8765
```

## What it shows

- Backend: online/version, worker, queue, job counts
- Local web: `/web/analysis.html` reachability and latency
- Cloudflare Tunnel: cloudflared, profile, PID, local port, listening, public URL
- Cloudflare Pages: configured URL and HTTP status
- LLM: enabled/model, Ollama online, model present/loaded
- SQLite: schema version, size, job counts, recent failed/needs_operator jobs
- Git: branch, commit, dirty state
- Logs: backend, tunnel, admin, plus a job-error table

## Settings

The only editable settings in Phase 1 are the LLM switch, model and URL. They are
written through the backend's `PUT /settings`, so they take effect for new jobs
without restarting the backend.

If the backend is offline, the panel reads `var/settings.json` for display but
will not save changes.

## Local API

All endpoints require `X-Admin-Token` and are bound to the local panel:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Backend/local web/tunnel/Pages/LLM/DB/Git status |
| GET | `/api/settings` | Backend settings, falling back to `var/settings.json` |
| PUT | `/api/settings` | Save LLM switch/model/URL through the backend |
| GET | `/api/llm/models` | Ollama model list |
| POST | `/api/llm/test` | Real screening test with the saved model |
| GET | `/api/logs?source={backend,admin,tunnel}` | Tail a known log |
| GET | `/api/job-errors` | Recent failed/needs_operator jobs |
| POST | `/api/tunnel/test` | Public URL HTTP self-check |

No endpoint starts or stops the backend. The bridge uses the existing FastAPI,
uvicorn and requests dependencies.

## Logs

- Backend: `var/logs/backend.log` (rotating, 5 MB × 3)
- Admin: `var/logs/admin.log` (rotating, 5 MB × 3)
- Tunnel: `tools/tunnel.log`

`var/` is ignored by Git. The log viewer only accepts the three source names; it
does not read arbitrary paths.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `DINELENS_ADMIN_TOKEN` | empty | Required admin token; also used by the backend `/settings` |
| `DINELENS_ADMIN_PORT` | `8765` | Panel port |
| `DINELENS_BACKEND_URL` | `http://127.0.0.1:8000` | Backend base URL |
| `DINELENS_LOG_DIR` | `var/logs` | Log directory |
| `DINELENS_SETTINGS_PATH` | `var/settings.json` | Settings file fallback |
| `DINELENS_DB_PATH` | `var/dinelens.sqlite3` | SQLite path |

## Security

- Bind only to `127.0.0.1`.
- Do not expose port 8765 through Cloudflare Tunnel or a public reverse proxy.
- The admin token is sent as `X-Admin-Token`; do not commit `var/admin_env.bat`.
- `.env`, `.env.*` and `*.local.json` are ignored, and `var/` covers the
  settings file, the admin token launcher and logs.
- Logs can contain review text; keep the panel local.

## Next updates

Phase 1 deliberately has no backend start/stop and no tunnel controls. The
following are the intended next steps.

### 1. One-click backend start/stop

Do **not** let the admin process directly `Popen`/`kill` uvicorn. The backend
owns a worker thread, SQLite connections and in-flight jobs; a hard kill can
leave tasks or the database in a bad state. Add a small **supervisor** process:

```text
Admin panel  →  backend_supervisor (127.0.0.1:8766)  →  uvicorn (127.0.0.1:8000)
```

The supervisor should:

- keep a PID file such as `var/backend.pid` and write the backend console log
  to `var/logs/backend.log`;
- start uvicorn with the project virtualenv, repository working directory and
  inherited `DINELENS_*` environment;
- check port 8000 before starting, and wait for `/status` before reporting
  "running";
- stop gracefully first (Ctrl+C / process-group signal), wait for the worker to
  finish its checkpoint, then offer a clearly labelled forced stop;
- optionally provide "stop after current job" using `/status` queue/job counts;
- detect crashes and restart with backoff only if that mode is explicitly
  enabled;
- expose only local endpoints such as:

```text
GET  /api/backend/status
POST /api/backend/start
POST /api/backend/stop
POST /api/backend/restart
```

The admin UI then adds Start / Stop / Restart buttons with a confirmation
dialog, a transition state and a link to the backend log. Keep the supervisor
local-only and require the same admin token.

Minimum tests for that change:

- double start does not create two listeners;
- port 8000 already occupied by a non-DineLens process;
- graceful stop during a queued job;
- forced stop after the graceful timeout;
- restart preserves the environment and the database;
- supervisor crash does not kill an already running backend.

A simpler intermediate option is a `stop_backend.bat` / `restart_backend.bat`
pair that sends Ctrl+C to the console owning the process. It is less polished
but avoids building a supervisor.

### 2. Tunnel controls

Reuse `tools/tunnel.py` rather than the Tkinter layer:

- start / stop / restart and guard mode;
- local service start/stop and port switching;
- profile switching, public self-check, tunnel log;
- run long operations in background threads and expose a transition state;
- require confirmation for stop/restart and never return Cloudflare
  credentials or `tunnels.json` contents.

### 3. Job operations

- Retry failed / `needs_operator` jobs with a reason (reuse
  `backend.manage retry` or add an admin-only endpoint).
- Show per-job timeline instead of only the final error; a `job_events` table is
  the clean way to store stage transitions.
- Add a "download diagnostics" bundle: job JSON, recent logs, settings summary,
  with secrets removed.

### 4. Settings beyond LLM

Add validated fields for:

- `score_method` (`mean_v1` / `bayesian_v1` / `bayesian_time_v1`);
- `share_days`, `raw_days`, `report_days`;
- `DINELENS_ORIGINS`, `DINELENS_COOKIE_SECURE`, `DINELENS_WEB_URL`.

Mark restart-required fields clearly. Retention is read when `Storage` is
constructed, so the panel must not imply that changing it is hot.

### 5. LLM upgrades

- Model presets and a benchmark button (screening + aspect samples, latency,
  JSON-failure rate).
- Configurable `num_ctx` / `num_predict`; the adapter currently hardcodes 8192
  and 768/1024.
- Per-job `llm_enabled` override for A/B runs.
- A visible fallback policy: when the model is down, drop uncertain reviews
  (current behavior) or retain them with a warning.
- Keep the rules-first routing; do not send clear retained/flagged reviews to
  the model just because a model exists.

### 6. Frontend deploy

- Run `tunnel.deploy_frontend(dry_run=True)` first and show the git status/diff.
- Require explicit confirmation before commit/push.
- Show the Pages URL and last commit; do not expose repository credentials.

### 7. Logs and observability

- Stream logs with SSE/WebSocket instead of polling every 2 seconds.
- Add per-job event logs and a level filter.
- Rotate/truncate `tools/tunnel.log`; it is currently unbounded.
- Add basic metrics: backend latency, Ollama latency, DB size, queue depth,
  recent failure rate.

### 8. Packaging and auth

- Optional Tauri/PWA wrapper around the same web UI; keep the web core.
- Move from the manual `X-Admin-Token` prompt to a short-lived admin session
  cookie + CSRF, with rate limiting.
- Keep the panel on `127.0.0.1`; if remote access is ever needed, put it behind
  Cloudflare Access or an SSH tunnel, not the public DineLens tunnel.
