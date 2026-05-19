# SDAI Digitalization

A full-stack document digitalization system for low-resource settings. It ingests scanned documents, extracts structured fields using a vision-language model (VLM), routes results through a three-tier confidence-based workflow, stores everything in a dynamically-inferred PostgreSQL schema, and exposes the data through a review queue, a document browser, and a live schema visualizer.

---

## What it does

1. **Ingest** — accepts documents via file upload, local filesystem path, or Google Drive folder. A preprocessing pipeline corrects orientation, removes flag-stripe watermarks, converts PDFs to PNG, and resizes to 1600 px.

2. **Extract** — runs `qwen2.5vl:7b` (via Ollama) on each document to produce a structured JSON object: document type, reference number, date, organisation, signatories, and quality flags. The VLM self-reports an extraction confidence tier (`high / medium / low`).

3. **Route** — a three-tier router writes results directly to the database (`auto_approved`) for high-confidence extractions, places medium- and low-confidence results in a human review queue (`review_required`), and marks crashes or parse failures for manual data entry (`manual_entry`).

4. **Review** — a human review queue presents each `review_required` document side-by-side with its editable extracted fields. Reviewers can approve (with corrections), reject, flag for manual entry, or skip. The queue is ordered oldest-first and supports keyboard navigation.

5. **Browse & visualize** — a paginated document browser queries the PostgreSQL tables with full-text search, date range, confidence, and review-status filters. A React Flow schema diagram shows all tables, columns, and foreign-key relationships, with live auto-refresh.

---

## Architecture

```
Document image
      │
      ├─ 1. Preprocessing ─── orientation fix · flag-stripe removal · PDF→PNG · resize
      ├─ 2. Classifier ─────── Tesseract on 400 px thumbnail · < 50 chars → skip
      ├─ 3. VLM Extraction ─── qwen2.5vl:7b via Ollama · structured JSON + confidence
      ├─ 4. Schema Inference ─ dynamic CREATE TABLE / ALTER TABLE per document type
      └─ 5. Router ──────────  high confidence  → auto_approved   → database
                               med / low        → review_required → review queue
                               error / crash    → manual_entry    → manual handling
```

The application layer is a FastAPI backend (`apps/api/`) consumed by a React + Vite frontend (`apps/frontend/`). See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full pipeline description, routing table, and technology stack.

---

## Quick start with Docker

```bash
git clone <repo-url> && cd sdai_digitalization
cp .env.example .env          # edit AUTH_SECRET_KEY at minimum
docker compose up --build     # downloads qwen2.5vl:7b on first boot (~5 GB)

# Create the first admin user (run once, after the stack is healthy)
docker compose exec api python create_admin.py \
  --email admin@example.com --password yourpassword
```

The dashboard is available at **http://localhost**. The API is at **http://localhost/api** (proxied internally by Nginx; expose port 8000 in docker-compose for direct access).

> **Ollama is now containerised.** The stack includes an `ollama` service that starts automatically. If you prefer to use a host-installed Ollama instead, change `OLLAMA_HOST=http://ollama:11434` in `docker-compose.yml` to `OLLAMA_HOST=http://host.docker.internal:11434` and remove the `ollama` service and `depends_on` entry from `api`.

---

## Offline deployment

For ministry servers or air-gapped environments with no reliable internet connection, use the two-script workflow.

### When to use this

| Scenario | Use |
|---|---|
| Internet-connected server, first deployment | `docker compose up --build` (standard quick start) |
| Air-gapped server, or unreliable connectivity | `setup.sh` + `install.sh` |
| Recurring deployment to many offline servers | Run `setup.sh` once, copy `./models/` to each server |

### Step 1 — Prepare on an internet-connected machine

```bash
./setup.sh
```

This takes 15–30 minutes and produces `./models/` (~7–10 GB total):

| File | Contents |
|---|---|
| `models/ollama-models.tar.gz` | `qwen2.5vl:7b` model weights and manifest |
| `models/docker-images.tar.gz` | All Docker images (`postgres`, `ollama`, `api`, `frontend`) |
| `models/wheels/` | Python wheels for `linux/amd64` (matching the API container) |
| `models/pnpm-store/` | Node package cache for local development |

### Step 2 — Transfer to the offline server

```bash
rsync -av --exclude '.git' . user@ministry-server:/opt/sdai_digitalization/
```

Or archive and copy manually:

```bash
tar czf sdai_bundle.tar.gz --exclude='.git' .
scp sdai_bundle.tar.gz user@ministry-server:/opt/
# On server: tar xzf sdai_bundle.tar.gz
```

### Step 3 — Install on the offline server

```bash
cd /opt/sdai_digitalization
./install.sh
```

This loads the Docker images, installs local dependencies, and starts the stack. The `ollama` container automatically restores the model from `models/ollama-models.tar.gz` on first boot — no internet required.

### Step 4 — Create the first admin user

```bash
docker compose exec api python create_admin.py \
  --email admin@ministry.td --password yourpassword
```

### How the offline model loading works

The `ollama` service in `docker-compose.yml` runs a shell script at startup that:

1. Checks if `./models/ollama-models.tar.gz` exists (mounted read-only at `/models`).
2. If yes, extracts it into the Ollama model store volume (`ollama_data`) — this is fast (no download).
3. Starts `ollama serve`.
4. If the model is not yet in the store (e.g., the tar wasn't present), falls back to `ollama pull` — this requires internet.

The `api` service waits for the `ollama` healthcheck to pass before starting, so processing begins only once the model is fully available.

---

## HTTPS

The `HTTPS_MODE` environment variable in `.env` controls which certificate is used. HTTP (port 80) always redirects to HTTPS (port 443).

| Mode | When to use |
|---|---|
| `self_signed` (default) | Offline ministry servers, intranet deployments, local testing |
| `letsencrypt` | Internet-connected servers with a public domain name |

### Mode 1 — `self_signed` (default, offline and intranet)

No configuration needed. A 10-year self-signed RSA certificate is generated once during `docker compose build` and baked into the image. The entrypoint uses it automatically.

```bash
# .env — nothing to set; self_signed is the default
HTTPS_MODE=self_signed
```

```bash
docker compose up --build
```

Accept the browser security warning once ("Your connection is not private") and proceed. The cert is stable across container restarts and rebuilds.

```
Dashboard:  https://localhost        (or https://<server-ip>)
API docs:   https://localhost/api/docs
```

Once HTTPS is confirmed working, enable secure cookies so the auth cookie is only sent over HTTPS:

```bash
# .env
AUTH_SECURE_COOKIES=true
```

```bash
docker compose restart api
```

### Mode 2 — `letsencrypt` (internet-connected servers)

Requires a public domain name that resolves to the server's IP and port 80 reachable from the internet.

**Step 1 — Configure `.env`**

```bash
HTTPS_MODE=letsencrypt
LETSENCRYPT_DOMAIN=sdai.example.gov.td
LETSENCRYPT_EMAIL=admin@example.gov.td
AUTH_SECURE_COOKIES=true
```

**Step 2 — Start the stack**

The frontend starts HTTP-only on first boot because no certificate exists yet:

```bash
docker compose up -d
```

**Step 3 — Issue the certificate (one-time)**

```bash
docker compose exec frontend certbot --nginx \
  -d "${LETSENCRYPT_DOMAIN}" \
  --email "${LETSENCRYPT_EMAIL}" \
  --agree-tos \
  --non-interactive
```

certbot completes the ACME HTTP-01 challenge through nginx, writes the certificate to the `letsencrypt_data` Docker volume, and reloads nginx with HTTPS automatically.

**Step 4 — Restart to activate the full HTTPS configuration**

```bash
docker compose restart frontend
```

On all subsequent restarts, the entrypoint detects the existing certificate and starts nginx with the HTTPS server block immediately.

**Renewal** (Let's Encrypt certs expire after 90 days):

```bash
docker compose exec frontend certbot renew
docker compose exec frontend nginx -s reload
```

Add to cron for automatic monthly renewal:

```cron
0 3 1 * * docker compose -f /opt/sdai_digitalization/docker-compose.yml exec -T frontend certbot renew --quiet && docker compose -f /opt/sdai_digitalization/docker-compose.yml exec -T frontend nginx -s reload
```

---

## Prerequisites

| Dependency | Minimum version | Notes |
|---|---|---|
| Docker + Compose | 24 / 2.20 | Compose v2 (`docker compose`, not `docker-compose`) |
| `qwen2.5vl:7b` model | — | Downloaded automatically on first boot via the containerised Ollama service |

Ollama runs as a Docker service — no host installation required. The model (~5 GB) is pulled automatically on first boot. For offline environments see the [Offline deployment](#offline-deployment) section.

---

## Backup

`backup.sh` backs up both the PostgreSQL database and the uploaded/processed files. The Docker stack must be running when the script executes.

**First time after cloning**, make the script executable:

```bash
chmod +x backup.sh
```

**Run manually:**

```bash
./backup.sh
```

Backups are written to `./backups/`:

| Output file | Contents |
|---|---|
| `backups/db_YYYY-MM-DD_HH-MM-SS.sql.gz` | Full PostgreSQL dump (documents, users, review queue, audit log) |
| `backups/images_YYYY-MM-DD_HH-MM-SS.tar.gz` | API data directory (processed images and uploaded files) |

The last 7 days of backups are kept automatically; older files are deleted on each run.

**Schedule with cron** (daily at 02:00 — add with `crontab -e`):

```cron
0 2 * * * /opt/sdai_digitalization/backup.sh >> /opt/sdai_digitalization/backups/backup.log 2>&1
```

For restore instructions, see [RESTORE.md](./RESTORE.md).

---

## Environment variables

Copy `.env.example` to `.env` and adjust as needed. Docker Compose reads this file automatically.

| Variable | Default | Description |
|---|---|---|
| `AUTH_SECRET_KEY` | `change-me-in-production` | JWT signing secret — **change this** |
| `AUTH_SECURE_COOKIES` | `false` | Set `true` when running behind HTTPS |
| `AUTH_TOKEN_EXPIRE_HOURS` | `8` | JWT lifetime in hours |
| `HTTPS_MODE` | `self_signed` | `self_signed` or `letsencrypt` — see [HTTPS](#https) |
| `LETSENCRYPT_DOMAIN` | *(empty)* | Public domain name; required when `HTTPS_MODE=letsencrypt` |
| `LETSENCRYPT_EMAIL` | *(empty)* | Email for Let's Encrypt expiry notices; required when `HTTPS_MODE=letsencrypt` |
| `DATABASE_URL` | `postgresql+asyncpg://sdai:sdai@localhost:5432/sdai` | asyncpg connection string; use `@db:5432` inside Docker |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string; use `redis://redis:6379` inside Docker |
| `OLLAMA_HOST` | `http://ollama:11434` | Points to the containerised Ollama service. Change to `http://host.docker.internal:11434` to use a host-installed Ollama |
| `DATA_DIR` | `./data` | Writable directory for processed images and uploaded files |
| `CORS_ORIGINS` | *(empty)* | Comma-separated allowed origins. Leave empty in Docker (Nginx proxies internally). Set to `http://localhost:5173` for local dev |
| `PROJECT_ROOT` | *(auto)* | Directory containing `documents/`. Set to `/app` in Docker automatically |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | *(none)* | Path to a service-account JSON key with Google Drive read access (optional) |

Users are stored in PostgreSQL, not in environment variables. Use `create_admin.py` to seed the first admin account (see Quick start above).

---

## Running locally for development

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11 |
| Node.js | ≥ 18 |
| pnpm | ≥ 9 (`npm install -g pnpm`) |
| PostgreSQL | 15 (or use `docker compose up db`) |
| Ollama | latest |
| Tesseract | system package — `brew install tesseract tesseract-lang` on macOS |

### Backend

```bash
cd apps/api
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start the API (from apps/api/)
CORS_ORIGINS=http://localhost:5173 uvicorn api_server:app --reload --port 8000
```

### Frontend

```bash
pnpm install
pnpm dev          # React dev server at http://localhost:5173
```

### OCR benchmarking (optional)

```bash
cd apps/api && source venv/bin/activate
# Place document images in documents/anonymized_docs/
python ocr_test.py
# Results written to documents/ocr_results/json/
```

### VLM extraction scripts (optional — the ingestion API replaces these)

```bash
# Via Ollama (recommended)
ollama pull qwen2.5vl:7b
python vlm_ollama_test.py

# Via HuggingFace local model (~15 GB download on first run)
python vlm_local_test.py
```

---

## Repository structure

```
.
├── apps/
│   ├── api/                        ← FastAPI backend
│   │   ├── api_server.py           ← App entry point: router registration, auth routes
│   │   ├── auth.py                 ← DB-backed auth: CurrentUser, get_current_user, require_admin
│   │   ├── audit.py                ← log_action() helper — fires-and-forgets to sdai_audit_log
│   │   ├── ingest_router.py        ← POST /api/ingest/* — upload, path, Drive, status; DDL engine
│   │   ├── documents_router.py     ← GET  /api/db/*    — browser, schema, image
│   │   ├── review_router.py        ← /api/review/*     — queue, approve, reject, flag
│   │   ├── admin_router.py         ← GET  /api/admin/audit-log (admin only)
│   │   ├── create_admin.py         ← Seed script: python create_admin.py --email … --password …
│   │   ├── ocr_test.py             ← OCR benchmarking script
│   │   ├── vlm_ollama_test.py      ← VLM extraction via Ollama
│   │   ├── vlm_local_test.py       ← VLM extraction via HuggingFace
│   │   ├── requirements.txt        ← Production Python deps
│   │   ├── requirements.test.txt   ← Test-only deps (includes pytest-benchmark)
│   │   ├── Dockerfile
│   │   └── tests/
│   │       ├── conftest.py           ← fixtures, make_result(), mock_db
│   │       ├── test_auth.py          ← login, logout, token blocklist, admin guard
│   │       ├── test_cache.py         ← cache key builder, invalidate_cache()
│   │       ├── test_documents.py     ← /api/db/* browse + schema
│   │       ├── test_ingest.py        ← upload, path, status, dedup helpers
│   │       ├── test_middleware.py    ← X-Process-Time header + slow-request WARNING
│   │       ├── test_performance.py   ← benchmark tests (opt-in with -m benchmark)
│   │       ├── test_rate_limit.py    ← SlowAPI key builder
│   │       └── test_review.py        ← approve, reject, flag, count
│   └── frontend/                   ← React + Vite + Tailwind + TanStack Query
│       └── src/
│           ├── components/
│           │   └── auth/
│           │       ├── ProtectedRoute.tsx   ← Redirects unauthenticated users to /login
│           │       └── AdminRoute.tsx       ← Redirects non-admins to /
│           ├── pages/
│           │   ├── DashboardPage.tsx        ← OCR / VLM comparison dashboard
│           │   ├── UploadPage.tsx           ← File upload, path, Google Drive ingest
│           │   ├── DocumentsPage.tsx        ← Paginated document browser
│           │   ├── DocumentDetailPage.tsx   ← Pan/zoom image + extracted fields
│           │   ├── ReviewPage.tsx           ← Human review queue (Tier 2)
│           │   ├── SchemaPage.tsx           ← React Flow live schema diagram
│           │   └── AuditPage.tsx            ← Audit log viewer (admin only)
│           └── hooks/
│               ├── useDocuments.ts
│               ├── useVlm.ts
│               ├── useIngest.ts
│               ├── useDocumentsDb.ts
│               ├── useReview.ts
│               ├── useSchema.ts
│               └── useAudit.ts
├── packages/
│   ├── types/                      ← @sdai/types — Zod schemas (shared)
│   └── api-client/                 ← @sdai/api-client — typed fetch wrappers
├── preprocessing/
│   └── anonymize.py                ← Interactive OpenCV redaction tool
├── documents/                      ← Mounted as volume; never baked into images
│   ├── anonymized_docs/            ← Source document images
│   └── ocr_results/
│       ├── json/                   ← OCR + VLM result JSON files
│       └── preprocessed/           ← Binarised images
├── setup.sh                        ← Prepare offline bundle on internet-connected machine
├── install.sh                      ← Deploy from bundle on offline server
├── backup.sh                       ← Daily backup: PostgreSQL dump + data directory tarball
├── docker-compose.yml
├── .env.example
├── pytest.ini
├── ARCHITECTURE.md
├── RESTORE.md                      ← Step-by-step restore instructions
└── CLAUDE.md
```

---

## API reference

All routes require authentication via a JWT cookie set by `POST /api/auth/login`. Interactive documentation is available at **http://localhost:8000/docs** when the API is running.

### Response headers

| Header | Origin | Description |
|---|---|---|
| `X-Process-Time` | FastAPI middleware | Wall-clock seconds the server spent on the request (4 decimal places, e.g. `0.0123`). Requests exceeding **2.0 s** also emit a `WARNING` log entry with the method, path, and duration. |
| `X-Cache-Status` | nginx | Present on `/images/*` responses. Values: `HIT` (served from nginx disk cache), `MISS` (fetched from FastAPI and cached), `EXPIRED`, `STALE`, `BYPASS`. Cached for **24 hours**; cache stored in `/var/cache/nginx` (up to 1 GB). |

### Auth

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Issue JWT, set httpOnly cookie |
| `POST` | `/api/auth/logout` | Clear auth cookie |
| `GET` | `/api/auth/me` | Return current user info |

### Ingestion (`/api/ingest`)

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/ingest/upload` | Upload one or more files (multipart/form-data) |
| `POST` | `/api/ingest/path` | Ingest from a local filesystem path or Google Drive folder ID |
| `GET` | `/api/ingest/status/{batch_id}` | Poll batch processing status |

`GET /api/ingest/status/{batch_id}` response includes `duplicates_skipped` — the count of files whose SHA-256 hash already exists in the database and were therefore skipped without VLM processing.

### Document database (`/api/db`)

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/db/types` | List all document types with row counts |
| `GET` | `/api/db/documents` | Paginated document list with filters (type, confidence, review status, date range, full-text search) |
| `GET` | `/api/db/documents/{table_name}/{id}` | Full field detail for one document |
| `GET` | `/api/db/schema` | Complete schema: tables, columns, types, foreign keys |
| `GET` | `/api/db/image` | Serve a processed image from DATA_DIR (path-traversal protected) |

### Review queue (`/api/review`)

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/review/count` | Pending review count (sidebar badge) |
| `GET` | `/api/review/queue` | Paginated queue of `review_required` documents, oldest first |
| `PATCH` | `/api/review/{table_name}/{id}` | Approve (with field corrections) or reject a document |
| `POST` | `/api/review/{table_name}/{id}/flag` | Escalate to `manual_entry`, remove from queue |

### Admin (`/api/admin`)

Requires admin role — HTTP 403 for reviewer accounts.

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/admin/audit-log` | Paginated audit log with filters (`action`, `date_from`, `date_to`, `user_id`, `page`, `page_size`) |

### OCR (legacy read-only)

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/documents` | List documents with OCR availability flag |
| `GET` | `/api/documents/{filename}` | Full OCR result (all engines × variants) |
| `GET` | `/api/results` | Combined `_all_results.json` |

### VLM (legacy read-only)

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/vlm/documents` | List documents with VLM availability flag |
| `GET` | `/api/vlm/documents/{filename}` | VLM extraction result for one document |
| `GET` | `/api/vlm/results` | All VLM results |

### Images

| Method | Route | Description |
|---|---|---|
| `GET` | `/images/raw/{filename}` | Original document image |
| `GET` | `/images/preprocessed/{filename}` | Binarised/preprocessed image |

---

## Testing

### Python — API server tests

Tests cover auth, ingest (including dedup helpers), document browsing, schema, review, middleware, caching, rate limiting, and auth-guard enforcement. The filesystem and database are fully mocked — no real documents, Postgres connection, or Ollama required.

```bash
cd apps/api
pip install -r requirements.test.txt

# Run all tests (excludes performance benchmarks)
pytest tests/ -m "not benchmark" -v

# Or from the repo root
pnpm test:api

# With coverage
pip install pytest-cov
pytest tests/ -m "not benchmark" --cov=api_server --cov-report=term-missing

# Run only performance benchmarks (requires a live-ish environment)
pytest tests/ -m benchmark -v
```

### TypeScript — Zod schema tests

Tests cover `parseVlmResult` (all tiers, error cases, field fallbacks) and all Zod schemas in `@sdai/types`.

```bash
pnpm --filter @sdai/types test    # schema tests only
pnpm test                          # all packages
```

### Run both

```bash
pnpm test && pnpm test:api
```

---

## Known limitations

- **GGML crash on certain images** — some documents trigger a segfault in the GGML backend. A 120-second asyncio timeout is applied per document; crashed documents receive `review_status = manual_entry`.
- **Self-reported VLM confidence** — the `high / medium / low` tier is taken from the model's own JSON output and has not been validated against human-labelled ground truth. Treat tier assignments as heuristic indicators.
- **Evaluation sample** — benchmarking results were produced on a 27-document sample. Performance on a broader corpus may differ, particularly for handwritten annotations and Arabic-dominant layouts.
- **Single worker** — the ingestion background worker processes one document at a time. Throughput scales with Ollama parallelism rather than with concurrency inside this application.

---

## Licence

MIT
