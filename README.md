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
      ├─ 2. Classifier ─────── ink-coverage check on 400 px thumbnail · blank page → skip
      ├─ 3. VLM Extraction ─── qwen2.5vl:7b via Ollama · structured JSON + confidence
      ├─ 4. Schema Inference ─ dynamic CREATE TABLE / ALTER TABLE per document type
      └─ 5. Router ──────────  high confidence  → auto_approved   → database
                               med / low        → review_required → review queue
                               error / crash    → manual_entry    → manual handling
```

The application layer is a FastAPI backend (`apps/api/`) consumed by a React + Vite frontend (`apps/frontend/`). See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full pipeline description, routing table, and technology stack.

---

## Customizing the extraction schema

The VLM extraction prompt is a **per-deployment schema**, not a fixed contract. The built-in default (used automatically whenever `VLM_PROMPT_FILE` is unset) is tailored to Chadian government administrative documents (`document_type`, `reference_number`, `signatory`, ...) — a different document corpus (invoices, a lexicon, land titles, medical records...) needs different fields entirely. There is currently no UI for this; it's a configuration step you do once per deployment, before ingesting that corpus.

**Starting point — the built-in default**: [`apps/api/prompts/examples/admin_document.txt`](./apps/api/prompts/examples/admin_document.txt) is an exact tracked copy of the prompt the pipeline uses out of the box (`_DEFAULT_VLM_PROMPT` in `apps/api/ingest_router.py`). If your corpus is reasonably close to administrative documents — a form with a handful of labeled fields, one page each, no repeating structure — copy this file, tweak the field names/descriptions for your document, and you likely don't need anything else on this page. If your corpus has a genuinely different *shape* (many repeating records per page, multi-page documents, a two-column layout, a legend of abbreviations to capture) — read on; that's exactly what the lexicon example below works through.

**Set environment variables** (see `.env.example`):

- `VLM_PROMPT_FILE` — path to a text file containing your full prompt. Put it under `documents/` (already volume-mounted into the API container) so it survives image rebuilds, e.g. `VLM_PROMPT_FILE=/app/documents/prompts/lexicon.txt`.
- `VLM_LIST_FIELDS` — comma-separated names of the fields in your schema that should **accumulate across pages** rather than take the first value found (see "Multi-page reconciliation" in [ARCHITECTURE.md](./ARCHITECTURE.md)). Get this wrong and a list field silently keeps only page 1's values.
- `VLM_PAGE_TIMEOUT_SECONDS` — per-page VLM call timeout (default 120). A schema that asks for a lot of output per page (e.g. a long list of dictionary entries) needs more generation time — raise this if pages fail with a VLM timeout.
- `SPLIT_PAGE_COLUMNS` — set to `true` if your document is typeset in two independent side-by-side columns (e.g. a dictionary, each entry self-contained within its column — **not** parallel-text translation, which needs both columns visible together to pair correctly). Splits each page down the middle before extraction, roughly halving content — and generation time — per VLM call.

No code change or rebuild needed — just set the vars and restart the `api` service (`docker compose up -d api`).

**Full worked example**: [`apps/api/prompts/examples/lexicon.txt`](./apps/api/prompts/examples/lexicon.txt) is a real, battle-tested prompt for a bilingual dictionary corpus (multi-page PDF, two-column layout, dense entries) — not a toy snippet. Copy it into `documents/prompts/` (or write your own there) and point `VLM_PROMPT_FILE` at it:

```bash
# .env
VLM_PROMPT_FILE=/app/documents/prompts/lexicon.txt
VLM_LIST_FIELDS=entries,table_of_contents,abbreviations
SPLIT_PAGE_COLUMNS=true
```

Without every accumulating field listed in `VLM_LIST_FIELDS`, reconciliation treats it as a scalar and keeps only page 1's value — every other page's content is silently dropped, exactly like the pre-fix blank-page bug this schema is meant to avoid.

**Lessons learned writing that example** (apply these to your own prompt):

- **Multi-line entries need explicit merging instructions.** A dictionary/glossary-style schema where one logical entry wraps across several printed lines will get *silently split into multiple broken entries* (empty fields, truncated text) unless the prompt explicitly explains how to recognize a continuation line vs. a new entry, ideally with a worked example. See the "CRITICAL — merging multi-line entries" paragraph in the example — this was the single highest-impact fix in this schema's development.
- **Tell the model to never emit an empty field.** An instruction like *"if you produce an entry with an empty field, that's a bug — merge it into the entry above instead"* gives the model a concrete self-check, and works far better than just describing the desired shape.
- **Capture the document's own legend/abbreviations, not just its content.** If your corpus uses abbreviations or codes (grammatical markers, status codes, etc.), add a field for them and instruct the model to extract that legend from wherever it appears (e.g. a "Signes et Abréviations" page) — otherwise the abbreviations littered through every entry are meaningless to anyone (or any model) consuming the data later.
- **Non-content fields shouldn't count toward a page's confidence.** A fixed field like `document_type` (repeated on every page, including blank/cover pages) shouldn't make a content-free page look like it "contributed" data — see `_NON_CONTENT_FIELDS` in `apps/api/ingest_router.py`'s `_reconcile_pages`, which excludes it from the "did this page have real content" check used for confidence aggregation across pages.
- **`SPLIT_PAGE_COLUMNS` only fits independent-column layouts.** It's for a dictionary/glossary where each column's entries are self-contained (right column doesn't need the left column's context) — *not* parallel-text translation (e.g. two columns of the same passage in different languages, meant to be read side by side), which needs both columns visible together to pair correctly. The pipeline auto-detects a genuine whitespace gutter per page before splitting, so full-width pages (covers, TOCs) in the same document are left whole.

Full-text search (`/documents` search box) is **not** hardcoded to any fixed field list — every TEXT-typed field your own prompt/schema extracts is automatically indexed and searchable, whatever your schema's field names are. The GIN index is rebuilt automatically whenever a table's columns change (`_ensure_table` in `apps/api/ingest_router.py`), and a one-time startup migration (`_backfill_fts_indexes`) widens the index on any table created before this behavior existed.

**Document links** — a document's detail page (`/documents/:table/:id`) has a "Related documents" section where any user can link two documents together (e.g. a sale deed "concerns" the original title deed it transfers), with a searchable picker and a free-text relation label. This builds a traceable chain of custody across otherwise-independent records — search finds one document, links let you follow it to the ones it came from or led to. Links are removed automatically when either document is permanently deleted.

---

## Quick start with Docker

```bash
git clone <repo-url> && cd sdai_digitalization
cp .env.example .env          # edit AUTH_SECRET_KEY at minimum
docker compose up --build     # downloads qwen2.5vl:7b on first boot (~5 GB)

# Create the first admin user (run once, after the stack is healthy)
# --tenant default: every deployment auto-seeds a 'default' tenant, so a
# standalone/single-ministry deployment (the common case) never needs to
# think about tenants beyond this — see "Multi-tenancy" below only if you
# plan to host more than one ministry on this instance.
docker compose exec api python create_admin.py \
  --email admin@example.com --password yourpassword --tenant default
```

The dashboard is available at **http://localhost**. The API is at **http://localhost/api** (proxied internally by Nginx; expose port 8000 in docker-compose for direct access).

> **Ollama is now containerised.** The stack includes an `ollama` service that starts automatically. If you prefer to use a host-installed Ollama instead, change `OLLAMA_HOST=http://ollama:11434` in `docker-compose.yml` to `OLLAMA_HOST=http://host.docker.internal:11434` and remove the `ollama` service and `depends_on` entry from `api`.

### First steps after install

1. **Log in** at `/login` with the admin account created above.
2. **Upload a document** at `/upload` — drag and drop a file (or ingest from a local path / Google Drive folder). This runs the full pipeline: preprocessing → VLM extraction → schema inference → confidence-based routing.
3. **Check the review queue** at `/review` — anything not auto-approved (medium/low confidence) lands here for a human to approve (with corrections), reject, or flag for manual entry.
4. **Browse ingested documents** at `/documents` — paginated, searchable/filterable view over the PostgreSQL-backed tables. Click a row for full field detail.
5. **Inspect the schema** at `/schema` — live diagram of every dynamically-created table, its columns, and foreign keys.
6. **Review the audit log** (admin only) at `/admin/audit` — every login, approval, rejection, and schema change, with filters and CSV export.

> The default `/` dashboard (VLM Extraction / OCR Comparison tabs) is a **legacy benchmark viewer**, not part of the ingestion pipeline above — it reads static JSON files produced by the standalone scripts in [OCR benchmarking](#ocr-benchmarking-optional) and [VLM extraction scripts](#vlm-extraction-scripts-optional--the-ingestion-api-replaces-these), and will show "results not found" until those are run manually. Skip it for normal use.

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
  --email admin@ministry.td --password yourpassword --tenant default
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
| Memory allocated to Docker | 16 GiB+ | See note below — this is a common source of silent ingestion crashes |
| `qwen2.5vl:7b` model | — | Downloaded automatically on first boot via the containerised Ollama service |

Ollama runs as a Docker service — no host installation required. The model (~5 GB) is pulled automatically on first boot. For offline environments see the [Offline deployment](#offline-deployment) section.

> **Memory matters.** `qwen2.5vl:7b` is ~6 GB on disk and needs meaningfully more RAM than that to run (weights + KV cache + inference overhead), on top of Postgres, Redis, the API, and nginx all sharing the same Docker VM. If Docker is only given the default ~8 GiB, the OS will silently kill the model process mid-inference — documents will show `status: crashed` with an error like `Server error '500 Internal Server Error' for url 'http://ollama:11434/api/chat'`, and Ollama's own logs (`docker compose logs ollama`) will show `llama-server process has terminated: signal: killed`. That signature means **out of memory**, not a bug. On Docker Desktop: Settings → Resources → Memory, raise to at least 16 GiB, apply, and restart Docker Desktop.

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

## Multi-tenancy

This platform supports two deployment models from the same codebase, so a ministry that needs to run fully independently and a shared instance serving several ministries are both first-class, not a fork or a different branch:

- **Standalone / autonomous** — one ministry, one instance (e.g. a ministry like Defense that needs to run fully independently). This is the default: every deployment auto-seeds a single `default` tenant, so if you only ever run `create_admin.py --tenant default`, tenancy is invisible and everything behaves exactly as a single-tenant deployment always has.
- **Shared instance** — multiple ministries on one instance (e.g. Land, Oil, Finance), each with its own documents, extraction schema, and users, fully isolated from each other. Each ministry's dynamically-created tables are **physically separated** (`t_<tenant-slug>_<document_type>`, e.g. `t_land_titre_foncier` vs. `t_oil_contrat`) rather than filtered rows in one shared table, so a bug in a query returns nothing instead of leaking another ministry's data. A ministry can log in, search its own land titles, contracts, or HR records by whatever fields its own extraction schema defines, without ever seeing another ministry's tables — this is the "automatic data schema load" idea: each tenant gets its own inferred schema from its own documents.

### Why provisioning a tenant is an engineer/ops action, not an in-app screen

Creating a tenant and editing its extraction prompt are both **deliberately** CLI-only, requiring shell access to the server — there is no "add ministry" or "edit schema" button anywhere in the app, and that's intentional, not a missing feature:

- **The prompt is the extraction schema.** A wrongly-edited prompt (a typo in the JSON shape, a dropped field, broken instructions) doesn't fail loudly — it silently degrades or breaks extraction for *every document that ministry ingests afterward*, often in ways that only show up as garbage/empty fields much later. That's not something a non-technical ministry admin should be able to trigger by editing a text box.
- **Keeping it file- and CLI-based** means every schema change is a deliberate, reviewable, ops-executed action (edit a file, run a script) — the same trust boundary `create_admin.py` already established for user creation, just extended to tenants and their schemas.
- Once a tenant exists, day-to-day use (uploading documents, reviewing, searching) is fully self-service for that ministry's own users — only *provisioning a tenant* and *changing its schema* require engineer-level access.

### Onboarding a new ministry onto a shared instance

```bash
# 1. Write the ministry's extraction prompt to a file under documents/
#    (already volume-mounted, survives image rebuilds — same mechanism as
#    the deployment-wide VLM_PROMPT_FILE, see "Customizing the extraction
#    schema" above). Base it on one of the tracked examples in
#    apps/api/prompts/examples/ (admin_document.txt or lexicon.txt).
mkdir -p documents/prompts
cp apps/api/prompts/examples/admin_document.txt documents/prompts/land.txt
# ... edit documents/prompts/land.txt for this ministry's actual fields ...

# 2. Create the tenant, pointing it at that prompt file.
docker compose exec api python create_tenant.py \
  --slug land --name "Ministry of Land" \
  --prompt-file /app/documents/prompts/land.txt

# 3. Create that tenant's first user (repeat for additional users/reviewers).
docker compose exec api python create_admin.py \
  --email land-admin@example.com --password yourpassword --tenant land
```

The land ministry's admin can now log in, upload documents, and everything (schema inference, search, review queue) operates only within `land`'s own tenant-prefixed tables — a second ministry can be onboarded the same way with a different `--slug` and its own prompt file, and neither will ever see the other's data.

### `create_tenant.py` reference

| Flag | Required | Meaning |
|---|---|---|
| `--slug` | yes | Short lowercase identifier (`^[a-z][a-z0-9_]{0,23}$`, max 24 chars) — becomes the table-name prefix `t_<slug>_...`. `default` is reserved (auto-seeded on every install); the script refuses that slug. |
| `--name` | yes | Display name, e.g. `"Ministry of Land"`. |
| `--prompt-file` | no | Absolute path (inside the container, e.g. `/app/documents/prompts/land.txt`) to this tenant's extraction prompt. Omit to inherit the deployment-wide `VLM_PROMPT_FILE` / built-in default. |
| `--list-fields` | no | Comma-separated field names unioned across pages for this tenant (same semantics as `VLM_LIST_FIELDS`). Omit to inherit the deployment-wide default. |
| `--page-timeout-seconds` | no | Per-page VLM call timeout override for this tenant. Omit to inherit `VLM_PAGE_TIMEOUT_SECONDS`. |
| `--split-page-columns` | no | Enable two-column page splitting for this tenant. Omit to inherit `SPLIT_PAGE_COLUMNS`. |

The script **upserts by slug** — re-running it with the same `--slug` updates that tenant's name/config (e.g. to point at a corrected prompt file) rather than creating a duplicate.

### Updating a ministry's schema later

Edit the tenant's prompt file directly (or re-run `create_tenant.py` with a different `--prompt-file`/`--list-fields`) — no rebuild or `docker compose restart api` needed. Tenant config is cached in-process for 30 seconds, so a file edit takes effect for the next document that ministry uploads within half a minute at most. As with the deployment-wide prompt, test a schema change against a couple of real documents from that ministry before trusting it broadly — see "Customizing the extraction schema" above for prompt-writing guidance (multi-line merging, never-empty-field rule, etc.), which applies identically to a per-tenant prompt file.

### Adding a user to an existing tenant

```bash
docker compose exec api python create_admin.py \
  --email reviewer@example.com --password yourpassword \
  --tenant land --role reviewer
```

`--role` defaults to `admin` if omitted. `create_admin.py` also upserts by email — re-running it for an existing user updates their password/role/tenant and re-activates the account.

### Verifying isolation

```bash
# As an authenticated user of tenant A, /api/db/types must list only
# tenant A's tables (all named t_<A's slug>_...) — never tenant B's.
curl -sk -b <tenant-A-cookies> https://<host>/api/db/types

# A cross-tenant lookup by a known table/id or batch id must 404, not
# succeed — confirms the tenant check on the ingest endpoints.
curl -sk -b <tenant-B-cookies> https://<host>/api/db/documents/<tenant-A-table>/<id>
```

### Known limitations

- No in-app or CLI "deactivate/delete tenant" flow yet — `sdai_tenants.is_active` exists in the schema but nothing sets it to `false` today; retiring a tenant currently means a manual `UPDATE sdai_tenants SET is_active = false WHERE slug = '...'` (which immediately blocks login for that tenant's users) and, if you also want its tables gone, a manual `DROP TABLE` per `t_<slug>_*` table.
- Full-text/structured search is still hardcoded to the default admin-document field names (`reference_number`, `organisation`, `destination_or_subject`, `signatory`) regardless of tenant — see the "Known limitation" note earlier in this doc. A tenant using a custom schema (e.g. a lexicon) can browse and filter its documents but not `?q=` full-text search them yet.

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
│   │   ├── create_admin.py         ← Seed script: python create_admin.py --email … --password … --tenant …
│   │   ├── create_tenant.py        ← Seed script: python create_tenant.py --slug … --name … (see "Multi-tenancy")
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
