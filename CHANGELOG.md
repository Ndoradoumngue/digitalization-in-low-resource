# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-05-19

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
- PostgreSQL-backed user management: `sdai_users` table, bcrypt password hashing (using `bcrypt` directly — no `passlib` dependency)
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

1. Update this file under a new `## [X.Y.Z] — YYYY-MM-DD` heading.
2. Commit: `git commit -m "chore: release vX.Y.Z"`.
3. Tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
4. Push: `git push origin main --tags`.
