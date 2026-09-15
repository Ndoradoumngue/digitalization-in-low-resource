# CONTEXT.md — Current Project State

This file captures what has been built, why, and what is known to be incomplete or pending. Update it as the project evolves.

---

## What Is Built

### Authentication

- JWT-based login via httpOnly cookie (`access_token=Bearer <token>`, `SameSite=Lax`).
- PostgreSQL-backed user management: `sdai_users` table with email, bcrypt password, role (`admin` | `reviewer`), and `is_active` flag.
- JWT includes a `jti` (UUID v4) for token blocklisting; logout writes `jti` to `sdai_token_blocklist`.
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`.
- `CurrentUser` dataclass (`id`, `email`, `full_name`, `role`, `tenant_id`, `tenant_slug`, `tenant_name`) returned by `get_current_user` dependency — the tenant fields come from a join to `sdai_tenants`, so a user always resolves to exactly one tenant.
- `require_admin` FastAPI dependency — raises HTTP 403 for non-admin users (admin is tenant-scoped, not global).
- `create_admin.py` seed script: `python create_admin.py --email admin@example.com --password secret --tenant default` (`--tenant` required; the tenant must already exist).
- React: `AuthContext` + `ProtectedRoute` + `AdminRoute` + `LoginPage`.
- All API data routes require a valid JWT — unauthenticated requests get HTTP 401.

### VLM Extraction Tab

- Reads `documents/ocr_results/json/vlm_qwen25_results.json` (output of `vlm_ollama_test.py`).
- Zod discriminated union on `extraction_confidence` → tier: `high | medium | low | failed`.
- Frontend renders `VlmPanel` — shows extracted fields keyed by confidence tier.
- Sidebar lists all filenames present in the VLM results JSON.

### OCR Comparison Tab

- Reads per-document JSON files from `documents/ocr_results/json/`.
- Shows side-by-side engine results (Tesseract, EasyOCR, etc.) via `OcrPanel`.
- Sidebar lists documents that have an OCR result file.

### Document Image Viewer

- Images served from `GET /images/raw/{filename}` and `GET /images/preprocessed/{filename}`.
- Rendered inside `OcrPanel` alongside the extracted text.

### Multi-Tenancy

- `sdai_tenants` table (`id`, `slug`, `name`, `prompt_file`, `list_fields`, `page_timeout_seconds`, `split_page_columns`, `is_active`). Every deployment auto-seeds a `default` tenant on startup, so a standalone/single-ministry deployment behaves exactly as before — tenancy only becomes visible once a second tenant is created.
- **Physical isolation, not row filtering**: each tenant's dynamically-created per-document-type tables are named `t_<tenant-slug>_<document_type>` (e.g. `t_land_titre_foncier`). `documents_router._get_tables_columns()` (the single discovery function nearly every endpoint routes through) filters by this prefix via `starts_with()`, so a missed filter returns nothing instead of leaking another tenant's rows.
- `sdai_users.tenant_id` and `batches.tenant_id` — every user belongs to exactly one tenant; `batch_documents`/`batch_document_pages` are *not* directly tenant-tagged (resolved via a JOIN through `batches` instead, to avoid duplicated/driftable columns).
- Per-tenant VLM config: `prompt_file` / `list_fields` / `page_timeout_seconds` / `split_page_columns` on `sdai_tenants` override the deployment-wide env vars (`VLM_PROMPT_FILE` etc.) for that tenant only, resolved via `ingest_router._get_tenant_config()` (30s in-process cache). NULL columns fall back to the env-var defaults — this is what makes the `default` tenant behave byte-identically to the pre-tenancy single-config deployment.
- Content-hash dedup and the ingest pipeline's Stage 1-3 (`_stage1_process`/`_stage2_worker`/`_stage3_process`) all thread `tenant_id`/`tenant_slug` end-to-end so a new document always lands in the uploader's own tenant's table.
- IDOR fix: `ingest_router.py`'s page/document/batch endpoints (previously reachable by any authenticated user via a guessed UUID, since they query `batches`/`batch_documents`/`batch_document_pages` directly rather than through the discovery layer) now check the resolved tenant against `current_user.tenant_id` and 404 on mismatch.
- Cache isolation: `cache._path_key_builder` includes `current_user.tenant_slug` in the Redis key (previously path-only, which would have let one tenant's cached `/api/db/types`/`/api/db/schema`/`/api/review/count` response leak to another tenant).
- Provisioning is CLI-only: `create_tenant.py --slug <slug> --name <name> [--prompt-file ...] [--list-fields ...] [--page-timeout-seconds ...] [--split-page-columns]`, then `create_admin.py --tenant <slug>`. No in-app "create tenant" UI in this version. `default` is a reserved slug.

### Generalized Search

- Full-text search and the document list's displayed fields are **not** hardcoded to the admin-document field names anymore (they were until this was built — see the now-resolved "Full-text search on custom schemas" entry that used to be in Known Limitations below). `documents_router._get_tables_columns()` returns `{table: {col: data_type}}` instead of `{table: {col, ...}}`; `_searchable_cols()`/`_extra_cols()` (documents_router.py) classify columns as "TEXT and not a system column" (searchable) vs. "not a system column, not document_type" (goes into the `extra_fields` JSONB blob every list/queue row now carries instead of 5 fixed named columns).
- `ingest_router._ensure_table()`'s GIN FTS index is built over every TEXT-typed VLM-extracted field at ingest time (`_BASE_COLS` frozenset excludes system columns), not a fixed 4-field tuple — mirrored exactly at query time so the WHERE clause always matches the index expression. A one-time startup migration, `_backfill_fts_indexes()` (called from `_init_db()`), rebuilds every existing table's index under this rule so pre-existing tenants/tables aren't stuck with the old narrower index.
- Frontend: `DbDocumentSchema`/`ReviewQueueItemSchema` (`packages/types/src/documents_db.ts`/`review.ts`) carry `extra_fields: Record<string, unknown>` instead of the 5 removed fields. `DocumentsPage.tsx`'s result table shows a single "Details" column summarizing `extra_fields` (`summarizeExtra()` in `apps/frontend/src/utils/format.ts`) instead of 3 fixed columns. `ReviewPage.tsx`/`ReviewForm` needed no changes — they already operated on the fully-dynamic per-document detail fetch, not the queue-item list shape.

### Document Links (chain-of-custody)

- `sdai_document_links` table (control-plane, not tenant-table-prefixed — `tenant_id` is a real column, like `batches`/`sdai_users`): `from_table`/`from_id` → `to_table`/`to_id`, a free-text `relation` (UI suggests "concerns"/"supersedes"/"transfers"/"amends"/"renews" but doesn't enforce a fixed vocabulary), optional `note`, `created_by`. Unique index on `(tenant_id, from_table, from_id, to_table, to_id, relation)` makes duplicate link requests idempotent (`ON CONFLICT DO NOTHING` + fallback SELECT returns the existing row rather than erroring).
- Endpoints in `documents_router.py`: `GET/POST /api/db/documents/{table}/{id}/links`, `DELETE /api/db/links/{link_id}`. Both `from_table`/`to_table` must be in the caller's own tenant's `_get_tables_columns()` result — this app-layer check is the entire tenant-isolation boundary for this feature, since a real FK against a dynamically-named table isn't possible. `DELETE` is `get_current_user`-level (any tenant user), not admin-only — removing a link doesn't destroy extracted data.
- Cascade-delete: hard-deleting a document (`review_router.delete_document`, admin-only; `ingest_router._delete_document_by_batch_document_id`, the re-upload/dedup-replace path) also deletes any links referencing it, via a shared `documents_router._delete_links_for_document()` helper (imported into `ingest_router.py` via a lazy import to avoid a circular dependency, same pattern already used for `auth`/`audit`'s use of `ingest_router._engine`), in the same transaction as the document delete.
- Frontend: `LinksSection.tsx` (mounted on `DocumentDetailPage.tsx`, below the field list) shows "Relates to"/"Referenced by" groups; `DocumentLinkPicker.tsx` is a from-scratch searchable picker (no reusable autocomplete component existed before this) reusing the generalized-search `fetchDbDocuments({q})` call.
- **Known gotcha already fixed once**: `_INIT_DDL` is naively split on `;` by `_init_db()` — a semicolon inside a `--` SQL *comment* (not just inside a `DO $$` block, the previously-known gotcha from the multi-tenancy migration) silently breaks this and produces a confusing SQLAlchemy/asyncpg `TypeError` on startup, not a clear SQL error. Hit and fixed during this feature's development; no semicolon characters are allowed in any comment line inside the `_INIT_DDL` string.

### Document Upload & Ingestion

- `POST /api/ingest` — accepts multipart file upload, stores document in PostgreSQL.
- Dynamic schema management: `_ensure_table()` creates or alters tables based on extracted fields.
- Extracted fields stored in per-type tables (e.g., `doc_invoice`, `doc_permit`).
- `UploadPage` at `/upload` with drag-and-drop file selector.
- **Content-hash deduplication**: SHA-256 hash computed before preprocessing. If an identical file was previously ingested, the pipeline is skipped and the document is recorded with `status='duplicate'`. The `GET /api/ingest/status/{batch_id}` response includes `duplicates_skipped` count. The Upload page shows a violet duplicate badge and counter.
- All per-type document tables have a `content_hash TEXT` column with a unique index (`idx_{table_name}_content_hash`) to enforce deduplication at the database layer.

### Document Database Browser

- `GET /api/documents/types` — returns list of document tables with record counts.
- `GET /api/documents/{table}` — paginated list of records in a given table.
- `GET /api/documents/{table}/{id}` — single document detail.
- `DocumentsPage` at `/documents` — searchable/filterable list with counts per type.
- `DocumentDetailPage` at `/documents/:tableName/:id` — full field display.

### Schema Inspector

- `GET /api/schema` — returns all per-type table schemas (columns, types).
- `SchemaPage` at `/schema` — table and column listing.

### Review Queue

- `PATCH /api/review/{table}/{id}` — approve (with optional field edits) or reject a document.
- `POST /api/review/{table}/{id}/flag` — flag a document for manual attention.
- `reviewed_by` column updated on approval/rejection.
- `ReviewPage` at `/review` — lists documents pending review, with approve/reject/flag actions.
- Review count badge in dashboard sidebar (red badge when items are pending).

### Audit Log

- `sdai_audit_log` PostgreSQL table: `id`, `user_id`, `user_email`, `action`, `table_name`, `document_id`, `details` (JSONB), `ip_address`, `created_at`.
- 8 action types logged automatically: `document_ingested`, `document_approved`, `document_rejected`, `document_flagged`, `user_login`, `user_logout`, `schema_created`, `schema_altered`.
- `audit.py` helper: `log_action()` never raises — failures are swallowed to stderr, never affecting callers.
- `GET /api/admin/audit-log` — admin-only endpoint with pagination (`page`, `page_size`) and filters (`action`, `date_from`, `date_to`, `user_id`).
- `AuditPage` at `/admin/audit` (admin-only, guarded by `AdminRoute`):
  - Action badge with color coding per action type.
  - Date range and action type filters.
  - Client-side CSV export (all items on current page).
  - Sticky table header, UUID truncation, IP address column.
  - Pagination footer.

### Offline Deployment

- `setup.sh` — run on an internet-connected machine to prepare `./models/` bundle:
  - Exports Ollama model data via Docker volume as `ollama-models.tar.gz`.
  - Saves Docker images as tarballs.
  - Downloads Python wheels and pnpm packages locally.
- `install.sh` — run on the offline server:
  - Loads Docker images, installs Python wheels and Node packages from local store.
  - Runs `docker compose up -d`.
- `docker-compose.yml` Ollama service:
  - Conditionally restores `ollama-models.tar.gz` if present (first-run offline restore).
  - Waits for Ollama to be ready, pulls model if not already loaded.
  - Health check gating: `api` service only starts after Ollama is healthy.

### Request Timing Middleware

- `api_server.py` includes an HTTP middleware that measures wall-clock time per request using `time.perf_counter()`.
- Every response carries an `X-Process-Time` header (4 decimal places, e.g. `0.0123`).
- Requests exceeding **2.0 s** emit a `WARNING` log: `Slow request: METHOD /path took X.XXXXs`.

### Nginx Image Caching

- All three nginx config files (`nginx.conf`, `nginx.letsencrypt.conf`, `nginx.letsencrypt_pending.conf`) include a `proxy_cache_path` at the `http` context (10 MB key zone, up to 1 GB on disk, 24-hour idle eviction).
- The `/images/` location block caches `200` responses for 24 hours; cache misses hit FastAPI once and subsequent requests are served from disk.
- The `X-Cache-Status` response header reports `HIT / MISS / EXPIRED / STALE / BYPASS`.

### Docker Deployment

- `Dockerfile.api`: Python 3.11 FastAPI, `documents/` mounted as volume.
- `Dockerfile.frontend`: multi-stage — pnpm build → nginx. nginx proxies `/api/` and `/images/` to the API container (no CORS needed).
- `docker-compose.yml`: `api`, `frontend`, `ollama` services on shared network.

---

## Architecture Decisions

| Decision | Reason |
|---|---|
| httpOnly cookie (not localStorage JWT) | Prevents XSS token theft |
| JWT `jti` blocklist in PostgreSQL | Allows immediate invalidation on logout |
| CORS disabled in Docker | nginx proxies internally — no cross-origin request |
| Zod discriminated union on `tier` | Guarantees exhaustive handling of confidence tiers in TypeScript |
| packages export `./src/index.ts` directly | No build step needed for workspace packages; Vite resolves them at dev time |
| `moduleResolution: "bundler"` | Required for Vite + TypeScript path aliases to resolve workspace packages correctly |
| `documents/` as a volume | Documents are not part of the source code; avoids bloating Docker images |
| Lazy imports in `auth.py` and `audit.py` | Breaks circular import: `ingest_router → auth → ingest_router` |
| `log_action()` swallows exceptions | Audit failures must never disrupt the calling endpoint's response |
| `_ensure_table()` returns `"created"/"altered"/"unchanged"` | Lets `_store_extraction()` fire schema audit events without duplicating the table check |
| Docker volume export for offline Ollama | `ollama save/load` don't exist — volume tarball is the only portable approach |
| SHA-256 dedup checked before preprocessing | Avoids VLM cost on duplicates; the hash is cheap relative to Ollama inference |
| `proxy_cache_path` before `server {}` in nginx conf | nginx includes these files inside its `http {}` block, so the directive is at the correct context level |
| `@pytest.mark.benchmark` for perf tests | Keeps the standard CI run fast; benchmarks opt-in with `pytest -m benchmark` |
| Tenant-prefixed tables, not a shared table + `tenant_id` column | Physical isolation — a missed `WHERE` clause returns nothing instead of leaking another ministry's rows; matches the "internal government archive" sensitivity level |
| Tenant/user provisioning is CLI-only (no in-app UI) | Keeps v1 scope controlled; onboarding a new ministry is a deliberate ops action, same pattern as `create_admin.py` already used |
| Per-tenant VLM config falls back to env vars when unset | Makes the auto-seeded `default` tenant byte-identical to the pre-tenancy single-config deployment — no behavior change for standalone deployments |

---

## Known Limitations / Not Yet Built

| Feature | Status |
|---|---|
| Document search / filter by field values | Not built — document list is unfiltered |
| VLM model selector | Not built — only `vlm_qwen25_results.json` is wired to the VLM tab |
| Field correction UI | Not built — document detail view is read-only |
| Mobile / responsive layout | Not tested — designed for desktop |
| Email notifications | Not built — review queue is pull-only |
| Bulk review actions | Not built — documents are approved/rejected one at a time |
| Audit log user filter in UI | Backend supports `user_id` filter but UI exposes only `action` and date range |
| Tenant deactivate/delete flow | `sdai_tenants.is_active` exists but nothing sets it to `false` — retiring a tenant currently means a manual SQL `UPDATE` |
| `serve_processed_image` (`/api/db/image`) not tenant-scoped | Serves a file by raw path within known ingest directories, with no check that the path belongs to the caller's tenant — lower severity since paths aren't guessable, but a gap flagged and not yet fixed |

---

## File Layout Reference

```
documents/                          # NOT in git, mounted at runtime
  raw/                              # original scanned images
  preprocessed/                     # pre-processed variants
  ocr_results/
    json/
      vlm_qwen25_results.json       # VLM batch results
      <filename>.json               # per-document OCR results

apps/api/
  api_server.py                     # FastAPI app, auth routes, X-Process-Time middleware
  auth.py                           # get_current_user, require_admin, CurrentUser dataclass
  audit.py                          # log_action() helper, client_ip()
  ingest_router.py                  # /api/ingest, dynamic DDL, _engine(), SHA-256 dedup
  documents_router.py               # /api/documents/* browse endpoints
  review_router.py                  # /api/review/* approve/reject/flag
  admin_router.py                   # /api/admin/audit-log (admin only)
  create_admin.py                   # seed script to create/upsert a user within a tenant
  create_tenant.py                  # seed script to create/upsert a tenant (ministry)
  tests/
    conftest.py                     # fixtures: auth_client, mock_db, make_result()
    test_auth.py                    # auth routes, token blocklist, admin guard
    test_cache.py                   # cache key builder, invalidate_cache()
    test_documents.py               # /api/db/* browse + schema endpoints
    test_ingest.py                  # /api/ingest/*, _compute_hash, _find_duplicate, dedup paths
    test_middleware.py              # X-Process-Time header + slow-request WARNING
    test_performance.py             # benchmark tests (excluded from CI with -m "not benchmark")
    test_rate_limit.py              # SlowAPI rate-limit key builder
    test_review.py                  # /api/review/* approve/reject/flag/count

apps/frontend/src/
  context/AuthContext.tsx           # global auth state (user, login, logout)
  components/auth/ProtectedRoute.tsx
  components/auth/AdminRoute.tsx    # redirects non-admins to /
  pages/LoginPage.tsx
  pages/DashboardPage.tsx           # main app shell — sidebar + tab switcher + panels
  pages/UploadPage.tsx              # file upload UI
  pages/DocumentsPage.tsx           # database document browser
  pages/DocumentDetailPage.tsx      # single document field display
  pages/SchemaPage.tsx              # schema inspector
  pages/ReviewPage.tsx              # review queue
  pages/AuditPage.tsx               # audit log (admin only)
  components/DocumentList.tsx
  components/OcrPanel.tsx
  components/VlmPanel.tsx
  components/LinksSection.tsx       # "Related documents" panel on DocumentDetailPage
  components/DocumentLinkPicker.tsx # searchable picker for creating a new link
  hooks/useDocuments.ts             # TanStack Query hooks for OCR
  hooks/useVlm.ts                   # TanStack Query hooks for VLM
  hooks/useDocumentsDb.ts           # hooks for database document browser
  hooks/useDocumentLinks.ts         # hooks for chain-of-custody links
  hooks/useIngest.ts                # upload mutation hook
  hooks/useReview.ts                # review actions + count hook
  hooks/useSchema.ts                # schema query hook
  hooks/useAudit.ts                 # audit log query hook
  utils/format.ts                   # labelFor(), summarizeExtra() — shared across pages/components

packages/types/src/
  auth.ts                           # UserSchema, LoginResponseSchema
  ocr.ts                            # OcrDocument, DocumentEntry, EngineResult schemas
  vlm.ts                            # ExtractionResult discriminated union + parseVlmResult()
  documents_db.ts                   # DbDocument (with extra_fields), DbType schemas
  ingest.ts                         # IngestResult schema
  review.ts                         # ReviewQueueItem (with extra_fields), ReviewPatch schemas
  schema.ts                         # SchemaInfo schemas
  audit.ts                          # AuditLogEntry, AuditLogPage schemas
  links.ts                          # DocumentLink, CreateLinkBody schemas

packages/api-client/src/
  index.ts                          # all fetch functions, imageUrl helper, fetchAuditLog()

setup.sh                            # internet-connected machine: prepare offline bundle
install.sh                          # offline server: install from bundle + docker compose up
```

---

## Suggested Next Steps

1. **Audit log user filter** — expose the `user_id` filter in `AuditPage` with a user email autocomplete.
2. **VLM model switcher** — allow selecting between multiple `vlm_*_results.json` files via a dropdown.
3. **Bulk review** — select multiple documents in `ReviewPage` and approve/reject in batch.
4. **Field correction** — allow users to edit extracted fields in `DocumentDetailPage` and save to the database.
5. **Email notifications** — notify reviewers when new documents are ingested and pending review.
6. **Auto-suggest document links** — when a VLM-extracted field (e.g. `reference_number`) matches an existing record, suggest a link during review instead of requiring the reviewer to search for it manually via `DocumentLinkPicker`.
7. **Tenant deactivate/delete flow** and **tenant-scoping `/api/db/image`** — see Known Limitations.
