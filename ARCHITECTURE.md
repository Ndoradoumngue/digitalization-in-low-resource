# Architecture

## System topology

```
                            Internet / LAN
                                  │
                             port 80/443
                                  │
                          ┌───────▼────────┐
                          │   frontend     │
                          │  nginx + React │
                          │  (SPA + proxy) │
                          └──┬────────┬───┘
                             │        │
                     /api/*  │        │  /images/*
                   /images/* │        │  (nginx cache — 24 h)
                             │        │
                     ┌───────▼────────▼───┐
                     │        api         │
                     │  FastAPI + Uvicorn │
                     │  port 8000         │
                     └──┬────────┬────────┘
                        │        │
              SQL (async)│        │ HTTP REST
              asyncpg    │        │ (Ollama API)
                         │        │
              ┌──────────▼──┐  ┌──▼──────────┐
              │     db      │  │   ollama    │
              │ PostgreSQL  │  │ qwen2.5vl:7b│
              │ port 5432   │  │ port 11434  │
              └─────────────┘  └─────────────┘
                        │
              ┌─────────▼───────┐
              │     redis       │
              │  response cache │
              │  port 6379      │
              └─────────────────┘
```

### Request flow

| Path | Route |
|---|---|
| `GET /` and all SPA routes | nginx serves `index.html` from the built React bundle |
| `POST/GET /api/*` | nginx proxies to `api:8000` — no round-trip to the internet |
| `GET /images/*` | nginx proxies to `api:8000/images/*`, caching `200` responses for 24 h |
| VLM inference | `api` calls `ollama:11434/api/chat` on the internal Docker network |
| Database reads/writes | `api` connects to `db:5432` via asyncpg (async SQLAlchemy) |
| Schema/types cache | `api` reads/writes Redis keys via `fastapi-cache2` |

### Port exposure

| Port | Exposed to | Service |
|---|---|---|
| 80 | Host | `frontend` — redirects to 443 |
| 443 | Host | `frontend` — HTTPS (self-signed or Let's Encrypt) |
| 5432 | Host (optional) | `db` — for local database tooling |
| 8000 | Internal only | `api` — accessed only via nginx proxy |
| 11434 | Internal only | `ollama` — accessed only by `api` |
| 6379 | Internal only | `redis` — accessed only by `api` |

---

## Five-stage pipeline

### 1 — Preprocessing

Each incoming document image passes through a deterministic preprocessing chain before any model sees it:

- **Orientation correction** — Tesseract OSD detects rotation in 90° increments and applies the inverse transform.
- **Flag-stripe removal** — an HSV colour mask blanks out the blue/yellow/red stripes of the Mauritanian national flag watermark that appears on official letterheads.
- **PDF → PNG conversion** — `pdf2image` (backed by Poppler) renders each page at 200 DPI before the image pipeline runs.
- **Resize** — images are resized to a maximum of 1600 px on the longest axis to stay within the VLM's context budget.

### 2 — Classifier

A cheap heuristic gate runs before the VLM to filter non-documents (cover sheets, blank pages, photographs):

- Tesseract is run on a 400 px thumbnail.
- If the extracted text is fewer than 50 characters the document is marked `out_of_scope` and processing stops.
- Documents that pass continue to VLM extraction.

### 3 — VLM extraction

`qwen2.5vl:7b` is called via the Ollama REST API (`POST /api/chat`) with a deterministic zero-shot prompt requesting a JSON object containing:

```
document_type · reference_number · date · person_names · functions
destination_or_subject · organisation · signatory · budget_line
language · bilingual_layout · quality_issues · extraction_confidence
```

The model self-reports `extraction_confidence` as `high`, `medium`, or `low`. A 115-second per-document timeout guards against GGML crashes.

### 4 — Dynamic schema inference

Rather than maintaining a fixed database schema, the system creates and evolves tables at runtime based on the keys returned by the VLM:

- The document type value (e.g. `ordre_de_mission`) becomes the table name after sanitisation to a safe PostgreSQL identifier.
- `CREATE TABLE IF NOT EXISTS` creates the table on first encounter.
- `ALTER TABLE … ADD COLUMN IF NOT EXISTS` adds columns as new field keys appear in subsequent extractions.
- Python lists are stored as `JSONB`; all other scalar values are stored as `TEXT`.
- Table existence is always verified against `information_schema` — table names are never taken raw from user input.

### 5 — Three-tier router

```
┌──────────────────┬───────────────────────┬──────────────────┐
│    Condition     │     review_status      │   Destination    │
├──────────────────┼───────────────────────┼──────────────────┤
│ confidence=high  │ auto_approved          │ Database         │
│ confidence=med   │ review_required        │ Review queue     │
│ confidence=low   │ review_required        │ Review queue     │
│ crash / timeout  │ manual_entry           │ Manual handling  │
│ parse error      │ manual_entry           │ Manual handling  │
│ < 50 chars text  │ out_of_scope           │ Skipped          │
└──────────────────┴───────────────────────┴──────────────────┘
```

---

## Pipeline flow diagram

```
    ┌──────────────────────────────────────────────────────────┐
    │                    Ingestion sources                      │
    │   File upload · Local filesystem path · Google Drive     │
    └─────────────────────────┬────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │       1. Preprocessing         │
              │  orientation · stripe removal  │
              │  PDF→PNG · resize to 1600 px   │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │       2. Classifier            │
              │  Tesseract on 400 px thumb     │
              └───────┬───────────────┬───────┘
                      │               │
                 ≥ 50 chars       < 50 chars
                      │               │
                      │               ▼
                      │         out_of_scope → stop
                      │
                      ▼
              ┌───────────────────────────────┐
              │     3. VLM Extraction          │
              │  qwen2.5vl:7b via Ollama       │
              │  structured JSON + confidence  │
              └───────────────┬───────────────┘
                              │
                   ┌──────────┴──────────┐
                   │  parse OK?          │
                   └──────────┬──────────┘
                        no ───┼──► manual_entry
                              │ yes
                              ▼
              ┌───────────────────────────────┐
              │    4. Schema Inference         │
              │  CREATE TABLE / ALTER TABLE    │
              │  per document_type value       │
              └───────────────┬───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │     5. Three-tier Router       │
              └────┬──────────┬──────────┬────┘
                   │          │          │
               high conf   med/low    crash /
                   │        conf      parse err
                   ▼          ▼          ▼
            auto_approved  review_   manual_
                DB         required   entry
                        Review Queue
```

---

## Technology stack

| Layer | Technology | Purpose |
|---|---|---|
| VLM | `qwen2.5vl:7b` (Ollama) | Structured field extraction from document images |
| OCR | Tesseract 4 (`fra+ara`) | Cheap classifier + legacy benchmarking |
| Image processing | OpenCV, Pillow, pdf2image | Preprocessing pipeline |
| Backend framework | FastAPI + Uvicorn | Async REST API |
| Auth | python-jose JWT, bcrypt | httpOnly cookie auth |
| Database driver | SQLAlchemy (async) + asyncpg | PostgreSQL async I/O |
| Database | PostgreSQL 15 | Dynamic document storage |
| Task queue | asyncio.Queue + background task | Single-worker ingestion queue |
| Frontend framework | React 18 + Vite + TypeScript | SPA dashboard |
| Routing | React Router v6 | Client-side navigation |
| Data fetching | TanStack Query v5 | Caching, polling, mutations |
| Schema validation | Zod v4 | Runtime type safety at API boundary |
| Schema diagram | React Flow + dagre | Live database schema visualisation |
| Styling | Tailwind CSS | Utility-first styling |
| Containerisation | Docker + Docker Compose | Reproducible deployment |
| Reverse proxy | Nginx | Static file serving + API proxying |

---

## Database design

Tables are never defined in migration files. Instead, `ingest_router.py` inspects each VLM JSON result and:

1. Calls `_sanitize()` on the `document_type` value to produce a valid PostgreSQL identifier.
2. Issues `CREATE TABLE IF NOT EXISTS "{table}" (id UUID PRIMARY KEY, ...)` with base columns.
3. For each key in the JSON result, issues `ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{col}" {type}`.

This means the database schema is a direct reflection of what the VLM actually returns, not a schema designed in advance. The `GET /api/db/schema` endpoint exposes this live schema to the frontend for the React Flow visualisation.

Table discovery throughout the codebase always goes through `information_schema.columns` — checking for the presence of `source_image_path` as a sentinel — rather than maintaining any in-memory registry of known tables.
