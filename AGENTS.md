# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**Excel 轻量工作站 (Excel Station)** — a local-first web app for working with large Excel files. Python/FastAPI backend with a single-file vanilla-JS frontend (`static/index.html`, ~2200 lines). Supports data browsing, pivot tables, data cleaning, cross-file diff, and AI-powered natural-language queries (NL2SQL) via DeepSeek / OpenAI / Qwen / Ollama.

## Running the App

```bash
# Install deps (Python 3.10+)
pip install -r requirements.txt

# Start dev server (auto-reload, opens browser)
python main.py

# Or directly:
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://127.0.0.1:8000. API docs at `/docs` (Swagger UI).

There are no tests or linters configured.

## Packaging

```bash
pyinstaller excel_station.spec
```

Produces a single `ExcelStation` binary. The `Config` class in `app/config.py` has special logic (`get_app_dir`, `get_resource_dir`) that detects `sys.frozen` and resolves `static/` and `_shared/` relative to the executable — any path-related change must preserve this dual-mode (dev vs frozen) resolution.

## Architecture

### Layering

```
routers/  →  services/  →  config.py (state)
              ↕
         data/ (SQLite files, uploads, ai_config.json)
```

- **`app/main.py`** — FastAPI app creation, mounts `/_shared` for fonts/JS libs (echarts, mermaid), registers all routers, serves `static/index.html` at `/`.
- **`app/config.py`** — Single `Config` dataclass-ish object. All paths (DATA_DIR, UPLOAD_DIR, DB_PATH, STATIC_DIR, SHARED_DIR) derive from `get_app_dir()`. DB type (`sqlite`/`mysql`) and MySQL creds come from env vars.
- **`app/models/schemas.py`** — Pydantic request/response models.

### Routers (all prefixed `/api/...`)

| Router | Prefix | Role |
|---|---|---|
| `upload.py` | `/api/upload` | Accepts file, creates async parse task, polls progress |
| `data.py` | `/api/data` | Paginated query, stats, filters, cell updates, CSV export, diff, pivot |
| `ai.py` | `/api/ai` | AI config CRUD, NL2SQL, smart chart suggestion |
| `system.py` | `/api/system` | Health / system info |
| `database.py` | `/api/database` | Runtime MySQL connection config from the UI |

### Services (the real logic lives here)

- **`excelParser.py`** — Multi-library Excel parser with fallback chain: `python-calamine` → `openpyxl`. Handles encrypted files (via `msoffcrypto`), `.xls` (via `xlrd`), and uses `polars` for fast CSV. Raises `FileEncryptedError` when a password is needed.
- **`database.py`** — `DatabaseService` is a static-methods class supporting **both SQLite and MySQL**. Table names are namespaced per session (`{session_id}_{table_name}` in MySQL). Uses thread-local connection caching for MySQL (`_thread_local.connections`). Per-session SQLite files live in `data/sessions/{session_id}.db`.
- **`taskManager.py`** — `ParseTaskManager` (`taskManager` singleton) runs Excel parsing in background threads, tracks `ParseProgress` per session. Upload returns a `sessionId`; the frontend polls `/api/upload/progress/{session_id}`.
- **`aiService.py`** — Provider pattern: `AIProvider` base with `DeepSeekProvider`, `OpenAIProvider`, `QwenProvider`, `OllamaProvider`. Uses stdlib `urllib.request` (no `httpx`/`requests`). `NL2SQLService` and `SmartChartService` build prompts with table schemas + sample rows, ask the LLM for SQL/chart specs, and execute/return results.

### Key Data Flows

- **Upload → Parse → Query**: file saved to `data/uploads/` → `taskManager.create_task` spawns thread → `ExcelParserService` reads file → `DatabaseService` writes each sheet as a table into `data/sessions/{session_id}.db` → frontend polls progress → user queries via `/api/data/{session_id}/query`.
- **NL2SQL**: frontend sends question + session_id → router loads table schema + 5 sample rows per table → `NL2SQLService.generate_sql` prompts LLM → returned SQL is validated (must start with `SELECT`) → executed via `DatabaseService.execute_sql` → rows returned.
- **AI config** is persisted to `data/ai_config.json` and cached in a module-level `_config_cache` in `routers/ai.py`.

### Frontend

Single page `static/index.html` — vanilla JS, no build step. Uses `_shared/js/echarts.min.js` and `_shared/js/mermaid.min.js` for charting. All UI state (sessions, current sheet, AI settings) is managed client-side and talks to the backend via `fetch`.

## Conventions Worth Knowing

- **CamelCase in JSON, snake_case in Python.** Pydantic models use camelCase field names (`sessionId`, `sheetName`, `pageSize`); Python code uses snake_case.
- **Session = a UUID prefix tied to one uploaded file's SQLite DB.** Everything data-related is keyed by `session_id`.
- **Table name sanitization**: sheet names go through `sanitize_table_name()` in `routers/data.py` (spaces/dashes → underscores) before hitting SQL.
- **Optional deps are guarded** with `HAS_CALAMINE`, `HAS_OPENPYXL`, `HAS_POLARS`, `HAS_PYMYSQL` flags — the app degrades gracefully.
- **No test suite, no linting config, no CI.** When adding tests, pick a framework and set it up from scratch.
- **AI providers use `urllib.request`** rather than SDKs — keep that pattern if adding new providers (no new deps).
- **Thread safety**: `taskManager` uses a `threading.Lock`; `DatabaseService` uses thread-local MySQL connections. The FastAPI app itself runs on asyncio but service code is sync.
