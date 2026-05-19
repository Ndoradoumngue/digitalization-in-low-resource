# CONTEXT.md — Current Project State

This file captures what has been built, why, and what is known to be incomplete or pending. Update it as the project evolves.

---

## What Is Built

### Authentication

- JWT-based login via httpOnly cookie (`access_token=Bearer <token>`, `SameSite=Lax`).
- PostgreSQL-backed user management: `sdai_users` table with email, bcrypt password, role (`admin` | `reviewer`), and `is_active` flag.
- JWT includes a `jti` (UUID v4) for token blocklisting; logout writes `jti` to `sdai_token_blocklist`.
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`.
- `CurrentUser` dataclass (`id`, `email`, `full_name`, `role`) returned by `get_current_user` dependency.
- `require_admin` FastAPI dependency — raises HTTP 403 for non-admin users.
- `create_admin.py` seed script: `python create_admin.py --email admin@example.com --password secret`.
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

### Document Upload & Ingestion

- `POST /api/ingest` — accepts multipart file upload, stores document in PostgreSQL.
- Dynamic schema management: `_ensure_table()` creates or alters tables based on extracted fields.
- Extracted fields stored in per-type tables (e.g., `doc_invoice`, `doc_permit`).
- `UploadPage` at `/upload` with drag-and-drop file selector.

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
  api_server.py                     # FastAPI app, auth routes, router registration
  auth.py                           # get_current_user, require_admin, CurrentUser dataclass
  audit.py                          # log_action() helper, client_ip()
  ingest_router.py                  # /api/ingest, dynamic DDL, _engine(), _INIT_DDL
  documents_router.py               # /api/documents/* browse endpoints
  review_router.py                  # /api/review/* approve/reject/flag
  admin_router.py                   # /api/admin/audit-log (admin only)
  create_admin.py                   # seed script to create/upsert admin user

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
  hooks/useDocuments.ts             # TanStack Query hooks for OCR
  hooks/useVlm.ts                   # TanStack Query hooks for VLM
  hooks/useDocumentsDb.ts           # hooks for database document browser
  hooks/useIngest.ts                # upload mutation hook
  hooks/useReview.ts                # review actions + count hook
  hooks/useSchema.ts                # schema query hook
  hooks/useAudit.ts                 # audit log query hook

packages/types/src/
  auth.ts                           # UserSchema, LoginResponseSchema
  ocr.ts                            # OcrDocument, DocumentEntry, EngineResult schemas
  vlm.ts                            # ExtractionResult discriminated union + parseVlmResult()
  documents_db.ts                   # DocumentRecord, DbType schemas
  ingest.ts                         # IngestResult schema
  review.ts                         # ReviewRecord, ReviewPatch schemas
  schema.ts                         # SchemaInfo schemas
  audit.ts                          # AuditLogEntry, AuditLogPage schemas

packages/api-client/src/
  index.ts                          # all fetch functions, imageUrl helper, fetchAuditLog()

setup.sh                            # internet-connected machine: prepare offline bundle
install.sh                          # offline server: install from bundle + docker compose up
```

---

## Suggested Next Steps

1. **Audit log user filter** — expose the `user_id` filter in `AuditPage` with a user email autocomplete.
2. **Document search** — add full-text or field-value search to `DocumentsPage`.
3. **VLM model switcher** — allow selecting between multiple `vlm_*_results.json` files via a dropdown.
4. **Bulk review** — select multiple documents in `ReviewPage` and approve/reject in batch.
5. **Field correction** — allow users to edit extracted fields in `DocumentDetailPage` and save to the database.
6. **Email notifications** — notify reviewers when new documents are ingested and pending review.
