# DineLens (食鉴)

**A trustworthy restaurant reputation assistant.** DineLens collects publicly available restaurant reviews, filters out advertising and malicious content with a local language model, summarizes what real diners consistently praise or complain about, and produces an explainable weighted score — one clear answer to the question: *is this restaurant worth visiting?*

This document describes how the program is built and how to use it.

---

## How It Works

```
Restaurant name + city
        │
        ▼
[1] Store search & disambiguation          AMap API / browser fallback
        │
        ▼
[2] Review collection                      Playwright + review endpoint
        │
        ▼
[3] Cleaning & screening                   dedup + local LLM filtering
        │
        ▼
[4] Classification & summarization         sentiment, aspects, extractive quotes
        │
        ▼
[5] Weighted score & report                Bayesian average + time decay
```

## Implementation Details

### 1. Store search and disambiguation

- Two interchangeable channels:
  - **AMap Web Service API** (`/v3/place/text`, `/v3/place/detail`) when an API key is configured;
  - **Browser fallback** (no key required): a real Chromium/Edge instance loads the map site's search page and the program captures the page's own JSON responses to extract the candidate list.
- Candidates are normalized to `id / name / address / city / rating / tel`; the target branch is confirmed manually or auto-picked by name/address matching.

### 2. Review collection

- Review data is fetched through the platform's own front-end endpoint. On AMap's PC site the comment module is hidden, but the endpoint it used is still available:

  ```
  GET /detail/get/reviewList?poiid=<poi_id>&pagesize=20&pagenum=<n>&select_mode=<mode>
  # mode: 1 = with photos, 2 = negative, 4 = all, 5 = latest
  ```

- Sessions are established by driving a real browser via **Playwright** with a persistent profile directory. All JSON responses seen by the page are intercepted and stored verbatim under `raw/` so results can be audited and re-parsed later.
- **Risk-control handling**: the program detects slider/login challenges (including ones rendered inside iframes), waits for manual completion when running in headed mode, then reuses the verified session for subsequent requests.
- Pagination follows `page_total` until exhausted; collected reviews are de-duplicated by `review_id`, with `(text, author, time)` as a fallback key.
- A Baidu Place API wrapper is included for store metadata. Manual review import remains deferred. Existing normalized JSONL outputs can be processed through the analysis CLI.

### 3. Storage and output

Each run writes to `out/<timestamp>-<store>/`:

| File | Content |
|---|---|
| `reviews.json` / `reviews.jsonl` / `reviews.csv` | normalized reviews (one per line in `.jsonl`) |
| `raw/` | raw JSON responses captured from the browser session |

Normalized review fields: `source, store, poi_id, author, rating, date, content, url`. The `.jsonl` file is the input format for the analysis stage.

### 4. Analysis engine

- `backend/analysis.py` consumes the existing normalized review dictionaries or `.jsonl` output. The default rules mode uses explicit promotional signals, English VADER and a Chinese domain lexicon. Local Ollama mode accepts a configured open-source model and validates its structured output. The pipeline provides:
  - ad / malicious / fake review screening with reasons;
  - positive/negative classification;
  - aspect tagging (food, service, price, atmosphere);
  - extractive summarization — the model may only quote original review text, never write new reviews.

### 5. Scoring

- Reviews that fail screening are excluded. The score combines:
  - **Bayesian weighted average** to correct small-sample distortion;
  - **Wilson interval** on the negative-review rate for confidence bounding;
  - **exponential time decay** so recent reviews weigh more.
- The report shows sample size, rating distribution, representative quotes, and what drives the score.
- The implemented default is `bayesian_time_v1`. Wilson bounds are reported separately for the negative-review rate. Zero valid ratings produce a null score, and 1–4 ratings carry a low-sample warning. Mean-only and Bayesian-only modes support comparison.

### 6. Web application (in progress)

- The FastAPI service in `backend/` connects the existing collectors to analysis and SQLite persistence. The single-worker queue supports cooperative cancellation while queued, collecting or analysing, checkpoint recovery, private history and revocable read-only sharing.
- Open `/web/analysis.html` on the local backend, or `analysis.html` on Pages. See [`backend/README.md`](backend/README.md) for session/CSRF handoff, configuration and API details, and [`docs/personal_delivery.md`](docs/personal_delivery.md) for validation and remaining external dependencies.

### 7. Tunnel tooling (`tools/`)

A companion manager for exposing the app from a home computer through **Cloudflare Tunnel** (no public IP, no port forwarding):

- GUI (`tunnel_gui.py`) and CLI (`tunnel.py`) with the same feature set;
- multi-profile support — each profile stores tunnel name, domain, credentials path, local port and edge settings; per-profile cloudflared configs are generated automatically at start;
- start/stop/restart, local-service switching, status dashboard, end-to-end self-check, log viewer, and a guard mode that reconnects automatically.

See [`tools/README.md`](tools/README.md) for setup and usage.

## Repository Layout

```
DineLens/
├── main.py                  # CLI entry: search / reviews / run
├── fetchers/
│   ├── browser_fetch.py     # Playwright: search, review fetching, risk handling
│   ├── amap_official.py     # AMap Web Service API wrapper
│   ├── baidu_official.py    # Baidu Place API wrapper
│   └── common.py            # storage & utility helpers
├── backend/                 # minimal job service (FastAPI + cancellation)
│   ├── app.py
│   ├── job_manager.py
│   ├── test_cancel.py
│   └── README.md
├── tools/                   # Cloudflare Tunnel manager (GUI + CLI)
│   ├── tunnel.py
│   ├── tunnel_gui.py
│   ├── README.md
│   └── *.example.json
├── config.example.json      # copy to config.json for official API keys
├── requirements.txt         # requests, playwright, fastapi, uvicorn
└── LICENSE
```

## Getting Started

Requirements: Python 3.10+ (Windows / macOS / Linux) and internet access.

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Official API keys are optional; without them the tool falls back to browser search:

```
# copy config.example.json to config.json, then fill in amap_key / baidu_ak
```

Typical usage:

```bash
# 1) search a restaurant and list candidates
python main.py search --source amap --keyword "楼外楼 杭州" --city-code 330100 --headed

# 2) fetch reviews for a specific store (--pages controls pagination)
python main.py reviews --source amap --poi-id B023B09325 --pages 10 --headed

# 3) one-shot: search -> pick -> fetch
python main.py run --source amap --keyword "店名 城市" --city-code 330100 --headed
```

Note: `--headed` opens a visible browser window so that a CAPTCHA/slider can be completed manually when a platform requires it; the verified session is stored in `browser-profile/` and reused afterwards.

To run the minimal job service and the cancellation demo:

```bash
python -m uvicorn backend.app:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/web/analysis.html` or `/docs`. `jobs-demo.html` continues to list and cancel owned jobs. Browser pages use an anonymous cookie and CSRF token, handled by `web/api.js`.

## Roadmap

- **Phase 1 — Data pipeline** (done): search, collection, de-duplication, export.
- **Phase 2 — Analysis engine**: rules and Ollama integration implemented, with conservative screening, constrained source quotations and real-model regression tests. Public sentiment benchmark results are recorded in `docs/validation/`. Screening quality evaluation awaits independently annotated reviews.
- **Phase 3 — Scoring & report**: weighted scoring, uncertainty bounds and explainable report pages implemented.
- **Phase 4 — Web application**: persistent jobs, three-stage cancellation, private history and controlled sharing integrated. Live source verification, production deployment and independent screening evaluation remain team integration work.

## Data & Compliance

- Collected data (which contains personal information such as review authors) is deliberately **not** committed to this repository; runtime output, browser profiles and local configuration are git-ignored.
- The tool is intended for personal use with low-frequency collection. Its analysis CLI can process the normalized JSONL files produced by the collectors.
- Anyone reusing this code is responsible for complying with the terms of the data sources and applicable laws.

## License

GPL-3.0 — see [LICENSE](LICENSE).

Built with open-source components including [Playwright](https://playwright.dev), [cloudflared](https://developers.cloudflare.com/cloudflare-one/), VADER, and configurable local models through Ollama.
