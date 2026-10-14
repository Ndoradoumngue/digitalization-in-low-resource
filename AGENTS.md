# AGENTS.md - Agentic Coding Guide

This file documents everything an AI coding agent needs to work effectively in this repository.

---

## Project Overview

**SDAI Digitalization** benchmarks OCR engines and Vision-Language Models (VLMs) for structured field extraction from scanned administrative documents - primarily Chadian government forms in French and Arabic. The goal is to find reliable digitalization pipelines for low-resource settings.

---

## Monorepo Structure

```
sdai_digitalization/
├── packages/
│   ├── types/          # @sdai/types  - Zod schemas shared by API client & frontend
│   └── api-client/     # @sdai/api-client - typed fetch wrapper (credentials: include)
├── apps/
│   ├── api/            # FastAPI backend
│   │   ├── api_server.py        # App entry point + auth routes + X-Process-Time middleware
│   │   ├── auth.py              # DB-backed auth: CurrentUser, get_current_user, require_* role guards
│   │   ├── audit.py             # log_action() - never raises, writes to sdai_audit_log
│   │   ├── ingest_router.py     # /api/ingest/* + _engine() + DDL helpers + SHA-256 dedup
│   │   ├── documents_router.py  # /api/db/*
│   │   ├── review_router.py     # /api/review/*
│   │   ├── versions_router.py   # /api/db/documents/{t}/{id}/versions|history (GED file versions, history)
│   │   ├── history.py           # field-history diff + insert - call from every path that changes fields
│   │   ├── classification_router.py  # /api/db series tree, tags, filing rules, link suggestions
│   │   ├── page_text.py         # page transcription (Tesseract) + sdai_fr search config (GED Phase 4)
│   │   ├── scripts/backfill_page_text.py  # transcribe pages without text (local node)
│   │   ├── document_files.py    # a document's file + download name + PDF watermark (GED Phase 5)
│   │   ├── share_router.py      # share links + the public /s/{token} routes (no auth - see CLAUDE.md)
│   │   ├── export_router.py     # /api/db/export selective export jobs (ZIP + sha256 manifest)
│   │   ├── lifecycle.py         # lifecycle state machine - the ONLY writer of lifecycle_status/trash (GED Phase 6)
│   │   ├── lifecycle_router.py  # /api/db archive, trash, restore, retention rules, legal holds, destructions
│   │   ├── retention.py         # retention rules (precedence, dates) + daily job (local node)
│   │   ├── destruction.py       # two-person destruction, tombstones, file deletion on both nodes, PV
│   │   ├── pdf_report.py        # small text-PDF writer (destruction PV)
│   │   ├── admin_router.py      # /api/admin/* (admin-only)
│   │   ├── sync/                # local/cloud sync (GED Phase 8)
│   │   │   ├── capture.py       # changelog triggers, row clocks, SYNC_TABLES, seed, node-local tables
│   │   │   ├── hlc.py           # hybrid logical clock format/rules (stamps made in SQL by sdai_hlc_now)
│   │   │   ├── apply.py         # applies a peer's changes (field-level last-writer-wins, conflicts)
│   │   │   ├── store.py         # cursors, epochs, serve_hello/push/pull, divergence -> reseed
│   │   │   ├── conflicts.py     # record / audit / resolve sync conflicts
│   │   │   ├── files.py         # file transfer primitives + file queue
│   │   │   ├── api.py           # /api/sync/* - node-to-node, HMAC-signed (sync/auth.py)
│   │   │   ├── admin.py         # /api/admin/sync/* - status, conflicts, files (admin)
│   │   │   ├── worker.py        # `python -m sync.worker` - the sync service
│   │   │   └── join.py          # `python -m sync.join` - prepare an empty node to receive a deployment
│   │   ├── create_admin.py      # Seed script for first admin account
│   │   └── tests/
│   │       ├── conftest.py          # fixtures: auth_client, admin_client, mock_db, make_result()
│   │       ├── test_auth.py         # login, logout, token blocklist, admin guard
│   │       ├── test_cache.py        # _path_key_builder, invalidate_cache()
│   │       ├── test_documents.py    # /api/db/* browse, schema, image endpoints
│   │       ├── test_ingest.py       # upload, path, status, _compute_hash, _find_duplicate, dedup
│   │       ├── test_middleware.py   # X-Process-Time header + slow-request WARNING log
│   │       ├── test_performance.py  # benchmark tests - opt-in with pytest -m benchmark
│   │       ├── test_rate_limit.py   # SlowAPI key builder
│   │       ├── test_review.py       # approve, reject, flag, count (delete = trash)
│   │       ├── test_lifecycle.py    # lifecycle guards, transitions, retention helpers, PV (mocked)
│   │       ├── test_lifecycle_pg.py # trash, retention, holds, destruction on a real PostgreSQL (-m pg)
│   │       ├── test_permissions.py  # role/flag/permission-level matrix + leak tests (mocked)
│   │       ├── test_access_pg.py    # access control on a real PostgreSQL (opt-in: -m pg + TEST_DATABASE_URL)
│   │       ├── test_versions.py     # versioning/fixity/history helpers (mocked)
│   │       ├── test_versions_pg.py  # versions, history, audit chain on a real PostgreSQL (opt-in: -m pg)
│   │       ├── test_sync_hlc.py / test_sync_api.py / test_sync_worker.py   # sync (mocked)
│   │       ├── test_sync_pg.py      # change capture on a real PostgreSQL (-m pg)
│   │       ├── test_sync_twonode_pg.py  # two databases, in-process transport: convergence, conflicts,
│   │       │                            # files, cloud uploads, restores (-m pg; also uses <db>_peer)
│   │       └── test_sync_e2e_pg.py  # two real API processes + the real worker over HTTP (-m pg)
│   └── frontend/       # @sdai/frontend - React 18 + Vite + Tailwind + TanStack Query
├── docker-compose.yml
├── docker-compose.cloud.yml   # cloud node override (no Ollama, ports 80/443) - use with -f both files
├── scripts/backup/     # backup service image: nightly restic backups + restore drill
├── RESTORE.md          # restore / rebuild procedures
├── Dockerfile.frontend
├── setup.sh            # Offline bundle preparation (internet-connected machine)
├── install.sh          # Offline install + docker compose up
├── pnpm-workspace.yaml
├── tsconfig.base.json
└── documents/          # NOT in git - mounted at runtime
```

### Package dependency graph

```
@sdai/frontend
  └── @sdai/api-client
        └── @sdai/types (zod schemas)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI 0.115, python-jose (JWT), passlib[bcrypt] |
| Frontend | React 18, TypeScript 5, Vite, Tailwind CSS v3, TanStack Query v5, React Router v6 |
| Schemas | Zod v4 (discriminated union on confidence tier) |
| Package manager | pnpm v10 (workspace) |
| Containerization | Docker - multi-stage frontend, nginx reverse proxy |
| Linting | ruff (Python), tsc (TypeScript), pre-commit |

---

## Key Commands

### Local Development

```bash
# Install all deps (run from repo root)
pnpm install

# Start the FastAPI API (port 8000)
cd apps/api && uvicorn api_server:app --reload --port 8000

# Start the React dev server (port 5173)
pnpm dev

# Type-check all packages
pnpm typecheck
```

### Docker

```bash
# Full stack (api on :8000, frontend on :80)
docker compose up --build

# Rebuild only one service
docker compose up --build api
docker compose up --build frontend

# Cloud node of a hybrid deployment (no GPU/Ollama)
docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d --build

# Backups
docker compose run --rm backup now            # one restic backup now
docker compose run --rm backup restore-test   # restore drill
```

### Tests

```bash
cd apps/api
pip install -r requirements.test.txt

# Standard run (excludes performance benchmarks)
pytest tests/ -m "not benchmark" -v

# Benchmark tests only
pytest tests/ -m benchmark -v

# Real-PostgreSQL suites against a THROWAWAY server (the tests wipe the schema of
# <scratch> and of <scratch>_peer, which the sync tests create for the second node)
TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/scratch pytest tests/ -m pg -v

# From repo root
pnpm test:api
```

Key test patterns:
- `mock_db` fixture - patches `ingest_router.engine`; configure with `mock_db.execute.return_value` or `mock_db.execute.side_effect`.
- `_make_fake_session(monkeypatch, fetchall=[...])` - patches `ingest_router.SessionLocal` for status endpoint tests.
- `asyncio.run(ingest_router._some_async_fn(...))` - call async helpers from sync test functions.
- Benchmark tests use `benchmark(fn)` for uncached endpoints and `benchmark.pedantic(fn, setup=InMemoryBackend._store.clear, rounds=N)` for cached ones. Do **not** use `benchmark.stats.mean` (broken in pytest-benchmark 4.0.0); use `time.perf_counter()` for timing assertions.
- Slow-request middleware test: patch `api_server.time` (not global `time`) so only `api_server.py`'s calls are affected.

### Pre-commit

```bash
pre-commit install        # install git hooks
pre-commit run --all-files  # run manually
```

---

## Architecture Conventions

### API (`apps/api/`)

- All data routes require `Depends(get_current_user)` - never expose data without auth. Prefer the role guards from `auth.py`, which all build on it: `require_user` (archive reads - any person, never an API key), `require_contributor` (upload/ingestion tracking - never a reader), `require_reviewer` (document actions), `require_access_manager`, `require_extraction_editor`, `require_admin`.
- Every query or check on a document goes through `documents_router._access_where_clause(table, user, permission)` / `_document_visible` / `_require_document_permission` with the level the action needs (`read`, `download`, `edit`) - never filter visibility in Python. Cached endpoints whose answer depends on visibility must be cleared (`_ACCESS_CACHE_NAMESPACES`) when grants, confidentiality or series change.
- Auth uses httpOnly cookies (`access_token=Bearer <jwt>`). No Authorization headers. Users are stored in PostgreSQL (`sdai_users`); JWT `jti` blocklist in `sdai_token_blocklist`.
- CORS is **disabled in Docker** (nginx proxies internally). Enable via `CORS_ORIGINS` env var for local dev only.
- `log_action()` in `audit.py` is called after state-changing operations. It swallows all exceptions - never let it propagate to callers. The audit log is hash-chained by a database trigger and append-only: never UPDATE/DELETE `sdai_audit_log` rows (the trigger refuses it and the chain verification would flag it).
- Any code that changes a document's extracted fields must record the change with `history.record_field_changes` (diff with `history.field_changes`) in the same transaction - see `documents_router._record_edit_history`. Files are never overwritten: a new file for a document is a new row in `sdai_document_versions`.
- **Local/cloud sync** captures every write to a synced table with database triggers - nothing to call from application code. But:
  - a new control-plane table that must exist on both nodes goes into `sync.capture.SYNC_TABLES` (in foreign-key order, with its primary key; `insert_only` for append-only tables) and into `_SYNCED_STATIC_TABLES` (sync columns + touch trigger); a table that must stay on one server goes into `NODE_LOCAL_TABLES`;
  - new file-path columns go into `sync.files.FILE_COLUMNS` / `LIST_FILE_COLUMNS` so the files follow their rows;
  - per-type tables are only created/altered through `_ensure_table` (it emits the ddl event the peer replays);
  - work that must happen once per deployment (numbering, extraction, scheduled jobs) runs on the local node only (`NODE_ROLE == "local"`; `_require_processing_node()` for routes, `_submit()` instead of `_queue.put`);
  - writes made on behalf of the peer run with `SET LOCAL sdai.applying_sync = on` (only `sync.apply` does this) - never set it elsewhere, those writes would not be synchronised.
- **Series tree**: never write `sdai_series.path` by hand outside `classification_router` - moves go through `PATCH /api/db/series/{id}` (cycle check + subtree rewrite), and series-scope access always goes through `documents_router._series_grant_match` (subtree inheritance).
- **Handing files out** (GED Phase 5): resolve a document's file with `document_files.resolve` (version rows - never `original_path`/`source_*` directly) and serve it through `check_servable` + `deliver` (safe roots, sync-pending answer, watermark). Never add a route without `get_current_user` besides `share_router.public_router` (`test_only_known_routes_are_unauthenticated` fails); public share checks go through `_usable_share` (creator re-checked on every use). Exports select documents with `permission="download"`, never `read`. Only the token hash is stored - never log or return a share token after creation.
- **Lifecycle** (GED Phase 6): never `DELETE` a document row and never write `lifecycle_status`, `deleted_at`/`deleted_by`/`deleted_reason` or `archived_at` outside `lifecycle.py` - trash with `lifecycle.trash` (routes: `lifecycle_router.trash_document`), destroy only through `destruction.execute` (approved record or trash purge). Legal holds are evaluated with `lifecycle.held_sql` at the moment of the change, never from the `legal_hold` column (a display copy). Code that changes a date a retention rule may depend on (fields, review, series, archiving) calls `retention.recompute(_quietly)` in its transaction. Files are only ever deleted by `destruction.purge_destroyed_files`; a new file-path column must be covered by `destruction.files_of`. Dedup and restore only consider live rows (`ingest_router._LIVE_HASH_PREDICATE`). Mocked tests: `mock_db` stubs the retention/hold hooks.
- **Full-text search**: every search expression uses the `sdai_fr` configuration (`documents_router._TSQUERY`, `_field_vector` - the field expression must stay identical to the index built by `_ensure_table`/`_backfill_fts_indexes`). Page rows are only created through `ingest_router._create_page_rows` (it queues their transcription); set a manual page's text through `_update_page_status(..., "manual", fields=...)`. Never select or write `batch_document_pages.ocr_tsv` - it is generated (and kept out of sync).
- `ingest_router.py` owns `_engine()`. Both `auth.py` and `audit.py` import it lazily (inside function bodies) to avoid the circular import: `ingest_router → auth → ingest_router`.
- **Deduplication**: `_compute_hash(path)` computes SHA-256 in 64 KB chunks; `_find_duplicate(hash)` queries all tables with a `content_hash` column. `_register_and_enqueue()` calls both before touching the queue. Duplicates get `status='duplicate'` in `batch_documents` and are counted in the `duplicates_skipped` field of `GET /api/ingest/status/{batch_id}`.
- Every per-type document table has a `content_hash TEXT` column with a unique index (`idx_{table_name}_content_hash`).
- **Middleware**: `add_process_time_header` in `api_server.py` adds `X-Process-Time` (4 d.p.) to every response and logs a `WARNING` for requests exceeding 2.0 s.
- VLM results are read from `documents/ocr_results/json/vlm_qwen25_results.json`.
- OCR results are read from `documents/ocr_results/json/`.
- Images served from `documents/raw/` and `documents/preprocessed/`.

### Nginx (`apps/frontend/`)

- Three config templates: `nginx.conf` (self-signed TLS), `nginx.letsencrypt.conf` (Let's Encrypt HTTPS), `nginx.letsencrypt_pending.conf` (HTTP-only pending first cert).
- All three include `proxy_cache_path` at the `http` level (before `server {}`) and a `/images/` location block with 24-hour caching and `X-Cache-Status` header.
- Only the pending config omits `X-Forwarded-Proto: https` (HTTP-only until cert is issued).

### Packages (`packages/`)

- TypeScript `moduleResolution: "bundler"` - packages export `./src/index.ts` directly, **no build step**.
- Path aliases in tsconfig map `@sdai/types` and `@sdai/api-client` to their `src/index.ts`.
- Do **not** add `rootDir` to `packages/api-client/tsconfig.json` - it breaks cross-package path resolution.
- All fetch calls use `credentials: "include"` for cookie-based auth.

### Frontend (`apps/frontend/`)

- `AuthContext` initialises by calling `GET /api/auth/me` on mount - sets `isLoading: true` until resolved.
- `ProtectedRoute` shows a spinner during auth init, then redirects to `/login` if no user.
- `AdminRoute` additionally redirects authenticated non-admin users to `/` (not `/login`). Use it for admin-only pages in `App.tsx`. `ContributorRoute` keeps read-only users off pages that upload or review; `LifecycleRoute` keeps everyone but lifecycle managers (admins, reviewers with `can_manage_lifecycle`) off the trash / retention / destruction pages.
- Hide actions the backend would refuse with `useDocumentPermissions(table, id)` (`hooks/useAccess.ts`) and `utils/permissions.ts` - the backend stays the authority.
- `DashboardPage` fetches both OCR and VLM document lists simultaneously; only the active tab's list is displayed.
- Tab state lives in `DashboardPage` - switching tabs clears `selected`.
- The admin-only Audit Log link in the sidebar is rendered only when `user?.role === "admin"`.

---

## Adding New Features

### New API route

1. Add the FastAPI route to the appropriate router (`ingest_router.py`, `documents_router.py`, `review_router.py`, `admin_router.py`) or create a new router and register it in `api_server.py`. Always include `Depends(get_current_user)`; use `Depends(require_admin)` for admin-only routes.
2. Add the Zod schema to `packages/types/src/` (new file or extend existing).
3. Add the fetch function to `packages/api-client/src/index.ts`.
4. Export from `packages/types/src/index.ts` if adding a new schema file.

### New frontend page

1. Create `apps/frontend/src/pages/MyPage.tsx`.
2. Add a `<Route>` in `apps/frontend/src/App.tsx` wrapped in `<ProtectedRoute>` (auth-gated), `<ContributorRoute>` (not for readers) or `<AdminRoute>` (admin-only).
3. Add a TanStack Query hook in `apps/frontend/src/hooks/` if the page fetches data.
4. Add navigation in the sidebar in `DashboardPage.tsx` or a new layout component. Wrap admin-only sidebar links in `{user?.role === "admin" && (…)}`.

### New VLM model

1. Add results JSON to `documents/ocr_results/json/vlm_<model>_results.json`.
2. Update `VLM_RESULTS_PATH` in `api_server.py` (or parametrize the route).
3. Verify the output matches `RawVlmResultSchema` in `packages/types/src/vlm.ts`.

---

## What to Avoid

- Do not bake `documents/` into Docker images - always mount as a volume.
- Do not use `allow_origins=["*"]` with `allow_credentials=True` in CORS - browsers reject it.
- Do not add `rootDir` to `packages/api-client/tsconfig.json`.
- Do not run `pnpm approve-builds` interactively during Docker builds - esbuild is pre-approved via `"pnpm": { "onlyBuiltDependencies": ["esbuild"] }` in root `package.json`.
- Do not skip `Depends(get_current_user)` (or a `require_*` guard built on it) on new data routes.
- Do not import `_engine` from `ingest_router` at module level in `auth.py` or `audit.py` - always import lazily inside the function body to avoid circular imports.
- Do not let `log_action()` failures propagate - the helper swallows exceptions by design.
- Keep schemas in `packages/types`, not duplicated in `api-client` or `frontend`.
- Do not change `DATA_DIR`, the `documents/` mount point or `INGEST_ROOT` on only one node of a hybrid deployment - files are referenced by absolute path and must have the same path on both nodes.
- Do not assign `record_id` or run extraction on the cloud node (`_next_record_id` raises there).
- Do not write to synced tables with triggers disabled (`session_replication_role = replica`, `ALTER TABLE ... DISABLE TRIGGER`) - the change would never reach the other node.

---

## Environment Variables (API)

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_SECRET_KEY` | `change-me-in-production` | JWT signing key - **change in production** |
| `AUTH_SECURE_COOKIES` | `false` | Set `true` in production (HTTPS) |
| `AUTH_TOKEN_EXPIRE_HOURS` | `8` | JWT TTL |
| `DATABASE_URL` | `postgresql+asyncpg://sdai:sdai@localhost:5432/sdai` | asyncpg connection string |
| `OLLAMA_HOST` | `http://ollama:11434` | Points to containerised Ollama; change to `http://host.docker.internal:11434` for host Ollama |
| `CORS_ORIGINS` | *(unset)* | Comma-separated allowed origins for local dev |
| `NODE_ID` / `NODE_ROLE` | `local` / `local` | This server in a hybrid deployment (`local` with GPU, or `cloud`) |
| `SYNC_NODE_TOKEN` | *(unset)* | Shared secret both nodes sign sync requests with - unset disables sync |
| `SYNC_PEER_URL` | *(unset)* | Only on the local node: the cloud node's URL |
| `SYNC_INTERVAL_SECONDS` / `SYNC_MAX_KBPS` | `30` / `0` | Sync cycle period / bandwidth cap (0 = none) |
| `SYNC_CHANGELOG_RETENTION_DAYS` / `SYNC_ALERT_HOURS` | `30` / `6` | Changelog pruning / admin alert threshold |
| `BACKUP_SFTP_TARGET`, `RESTIC_PASSWORD`, `BACKUP_NAS_PATH`, `BACKUP_TIME` | *(unset)* | `backup` service (restic) - see RESTORE.md |
| `TRASH_RETENTION_DAYS` / `TRASH_PURGE_BATCH` | `30` / `500` | Days a trashed document stays restorable before the purge (destruction) / documents purged per run |
| `RETENTION_JOB_INTERVAL_HOURS` | `24` | Retention job period (local node): dates, documents whose retention ended, trash purge |
| `DESTROYED_FILES_SWEEP_SECONDS` | `600` | Every node: delete files destroyed here or on the peer, heal tombstones |

Users and roles are stored in PostgreSQL, not in env vars. Use `create_admin.py` to seed the first admin account. `AUTH_USERNAME` and `AUTH_PASSWORD` no longer exist.
