# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added - GED Phase 1 (access rights)
- Read-only `reader` role (browse, search, download what it can see - never upload, review, edit, file or link) and delegated flags `can_manage_lifecycle` / `can_share`; per-user `clearance` (`public` < `internal` < `confidential` < `secret`). `create_admin.py --role reader`.
- Access grants by **series** and by **document type** in addition to per document, each with a permission level `read` < `download` < `edit`. Admin endpoints `GET/POST /api/db/grants`; `PATCH /api/db/access/{id}` changes a grant's level; re-granting the same grantee updates its level.
- Document confidentiality: `PATCH /api/db/documents/{t}/{id}/confidentiality` (access managers), audited as `confidentiality_changed`; a document above the user's clearance is invisible whatever the grants.
- `GET .../access` lists the document's own and inherited (series/type) grants and the caller's effective level (`my_permission`).
- `PATCH /api/admin/users/{id}` sets role (reviewer/reader), all delegated flags and clearance, audited as `user_permissions_changed`; `/api/auth/me` and login return the new flags and clearance.
- UI (fr/en): user editor with role, clearance and four flags; series and document-type grant editors; permission-level selectors; inherited grants and "your access" in the access panel; confidentiality badge and selector; actions hidden when the backend would refuse them; readers kept off Upload and Review.
- Tests: role/flag/permission matrix and leak tests (`test_permissions.py`), plus 23 opt-in tests against a real PostgreSQL (`test_access_pg.py`, `-m pg` with `TEST_DATABASE_URL`).

### Changed - GED Phase 1
- `_access_where_clause(table, user, permission="read")` rewritten: tenant-filtered EXISTS over document/series/type grants at the needed level, plus clearance and role caps; still a single SQL boolean expression.
- Existing grants migrated to scope `document`, level `edit` (unchanged behaviour); new grants default to `read`; the uploader auto-grant is `edit`. Uniqueness is now per scope (partial unique indexes replace the old table-wide constraint).
- Field edits, review actions, series assignment, links and page actions now need `edit` on the document; per-document export needs `download`. The review queue and count only list documents the user can edit.
- API keys are now upload/status only: archive endpoints and page actions (retry, reload, skip, cancel, pause, resume) reject them.
- Cache keys include the user (cached counts depend on each user's access).

### Security - GED Phase 1
- `/api/db/image` now checks that the caller may see the document the file belongs to (or, for files still in the ingestion pipeline, is a contributor of the same tenant); previously any file under the ingest directories was served to any authenticated user.
- Ingestion pages of a filed document follow that document's access.
- Links to a document the caller can't see no longer reveal its id, type, title or the link note.
- The admin audit log only shows the admin's own tenant.
- Cached `/types`, `/reviewers`, `/schema` and `/review/count` answers are no longer shared between users of a tenant.

### Added - GED Phase 0 (foundation)
- Document-level GED columns on every per-type table (`lifecycle_status`, `confidentiality`, `original_path`, `original_hash`, `current_version`, `deleted_at`/`deleted_by`, `retention_until`, `legal_hold`, `locked_by`/`locked_until`) plus sync columns (`updated_at`, `origin_node`, `hlc`) - added to new tables by `_ensure_table` and to existing ones at startup by `_backfill_ged_columns`.
- The untouched uploaded file is now recorded for every document (`original_path` + `original_hash`), image uploads included; existing documents are backfilled from `batch_documents.source_path`. The fixity check re-hashes it, so image-sourced documents get a real corruption check instead of an existence-only check.
- `sdai_touch_row` trigger keeps `updated_at`/`origin_node` current on every synced table (dynamic tables + 12 control-plane tables).
- Node identity: `NODE_ID` / `NODE_ROLE` env vars, `sdai_nodes` table, `node_id`/`node_role` in `/api/auth/me` and the login response, "Local server"/"Cloud server" badge in the sidebar.

### Changed
- `_access_where_clause` now also hides trashed (`deleted_at`) and destroyed rows - for admins too (`include_deleted=True` opts out). Admins still bypass access grants.
- GED columns can never be changed through field edits (`_PROTECTED_FIELDS`) and are excluded from search, the FTS index and `extra_fields`.
- Crashed-document retry keeps the document's untouched original instead of re-registering a preprocessed page image as its source.

---

## [1.0.0] - 2026-05-19

First stable release. Covers the full end-to-end digitalization pipeline from document upload through VLM extraction, review, and audit.

### Added

#### Ingestion pipeline
- Three-stage async pipeline: preprocessing → VLM extraction → schema inference + DB write
- Accepts multipart file upload, local server paths, and Google Drive folder IDs
- PDF → PNG conversion via `pdf2image` (Poppler)
- Orientation correction (Tesseract OSD), Chadian flag-stripe removal (OpenCV HSV mask), resize to 1600 px
- Tesseract heuristic classifier: documents with fewer than 50 characters are marked `out_of_scope`
- VLM extraction via `qwen2.5vl:7b` (Ollama REST); 115-second per-document timeout
- Three-tier routing: `auto_approved` (high confidence), `review_required` (med/low), `manual_entry` (crash/parse error)
- **Content-hash deduplication**: SHA-256 checked before preprocessing; duplicate files are recorded with `status='duplicate'` and counted in `duplicates_skipped`
- Dynamic PostgreSQL schema: `CREATE TABLE / ALTER TABLE` based on VLM output keys; `content_hash TEXT` column with unique index on every per-type table

#### Authentication & authorisation
- JWT-based login via httpOnly cookie (`access_token=Bearer <token>`, `SameSite=Lax`)
- PostgreSQL-backed user management: `sdai_users` table, bcrypt password hashing (using `bcrypt` directly - no `passlib` dependency)
- JWT `jti` blocklist in `sdai_token_blocklist`; logout immediately invalidates the token
- Two roles: `admin` and `reviewer`; `require_admin` FastAPI dependency enforces admin-only routes
- `create_admin.py` seed script

#### Document database browser
- Paginated document list with filters: type, confidence, review status, date range, full-text search
- Schema inspector: live table/column/type listing via `information_schema`; React Flow visualisation in the frontend
- Single-document detail view with image pan/zoom

#### Review queue
- Human review queue for `review_required` documents
- Approve (with optional field corrections), reject, or flag to `manual_entry`
- `reviewed_by` column updated on approval/rejection
- Pending-count badge in the sidebar (cached, 10-second TTL)

#### Audit log
- 8 action types: `document_ingested`, `document_approved`, `document_rejected`, `document_flagged`, `user_login`, `user_logout`, `schema_created`, `schema_altered`
- Admin-only paginated endpoint with filters: action type, date range, user ID
- CSV export from the UI

#### Middleware & observability
- `X-Process-Time` response header on every request (4 decimal places)
- `WARNING` log for requests exceeding 2.0 seconds: method, path, duration

#### Nginx image caching
- `/images/*` responses cached in nginx for 24 hours (up to 1 GB on disk)
- `X-Cache-Status` header: `HIT / MISS / EXPIRED / STALE / BYPASS`
- Applied to all three nginx config templates (`self_signed`, `letsencrypt`, `letsencrypt_pending`)

#### HTTPS
- `self_signed` mode (default): 10-year self-signed RSA cert baked into the Docker image at build time
- `letsencrypt` mode: certbot with the nginx plugin; HTTP-01 challenge; automatic renewal via cron
- HTTP (port 80) always redirects to HTTPS (port 443)

#### Offline deployment
- `setup.sh`: exports Ollama model weights, Docker images, Python wheels, and Node packages to `./models/`
- `install.sh`: offline install on an air-gapped server

#### Frontend
- React 18 + Vite + Tailwind CSS + TanStack Query v5 + React Router v6
- `AuthContext` + `ProtectedRoute` + `AdminRoute`
- Pages: Login, Upload (with duplicate badge), Documents, Document Detail, Schema, Review, Audit Log

#### Testing
- 137 unit/integration tests across 8 test files; 0 external dependencies (DB and filesystem fully mocked)
- 3 performance benchmark tests (`pytest -m benchmark`): document list, schema (cached vs. uncached), review count
- `pytest-benchmark 4.0.0` integrated; timing assertions use `time.perf_counter()` (not `benchmark.stats.mean`)

#### Documentation
- `README.md`, `AGENTS.md`, `CONTEXT.md`, `ARCHITECTURE.md` (pipeline + system topology diagrams)
- `CONTRIBUTING.md`, `DOCKER.md` (quickstart, HTTPS modes, offline deployment, common operations)
- `CHANGELOG.md` (this file)

---

## How to release a new version

1. Update this file under a new `## [X.Y.Z] - YYYY-MM-DD` heading.
2. Commit: `git commit -m "chore: release vX.Y.Z"`.
3. Tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
4. Push: `git push origin main --tags`.
