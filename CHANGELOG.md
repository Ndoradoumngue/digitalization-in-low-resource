# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added - GED Phase 7 (notifications, check-out, comments, electronic signature)
- Notifications: a bell with the unread count (refreshed every minute) and a Notifications page. People are notified when a document waits for their review (grouped into "N documents to review"), a document is shared with them, someone mentions them or replies to their comment, a retention period ends, a destruction awaits approval, their signature is requested (and the requester when everyone signed or someone declined), an admin releases their check-out, and (admins) when synchronisation is down or a backup failed. Notifications about a document the person can no longer open hide the document.
- E-mail: immediate, daily digest or off, per person, in their language (French/English). E-mails carry only a fixed sentence and a link (`PUBLIC_BASE_URL`), never document content. Settings `SMTP_*`, `EMAIL_*`. Each node sends the e-mails it queued, with retries; queueing the same e-mail twice is a no-op. Admins see failed e-mails on the Synchronisation page (alert + list) and can send them again or drop them.
- Check-out / check-in ("Extraire / Réintégrer"): a checked-out document is read-only for everyone else, admins included (fields, versions, review, tags, series, links, page actions, trash) with a "Verrouillé par X" answer; check-in can attach the new file as the next version with a comment; an admin can force the unlock (the holder is notified); check-outs expire (`CHECKOUT_DEFAULT_HOURS` 8 h, max `CHECKOUT_MAX_HOURS`). Between the two servers a check-out is advisory while they can't reach each other.
- Comments on documents: threads, replies, @mentions of people who can read the document, edit / delete (soft). Readers read, reviewers and admins comment - also on checked-out or archived documents.
- Electronic signature (simple / advanced, PAdES - never "qualified"): request signatures from one or more people in order with a reason; each signer re-enters their password and the current version is signed with the organisation's certificate (`SIGNING_P12_PATH`) with a visible stamp (name, title, date, reason), stored as a new version; image originals are converted to PDF (PDF/A with ocrmypdf) first. Verification of every embedded signature (unchanged, trusted, coverage) next to SDAI's evidence (signer, time, IP, certificate fingerprint, hashes) and the audit trail. Decline and cancel. `scripts/generate_signing_cert.py` creates a self-signed test certificate.
- Audit: `document_checked_out`, `document_checked_in`, `comment_added` / `_edited` / `_deleted`, `signature_requested`, `document_signed`, `signature_declined`, `signature_request_cancelled`, `signature_password_failed`, `notification_preferences_changed`, `emails_retried`, `emails_discarded`.

### Changed - GED Phase 7
- Refusals because of a check-out answer 423 (was 409 for new versions).
- A checked-out document leaves the other reviewers' review queue until it is checked in.
- Destroying a document also removes its comments, notifications, signature requests and signature evidence (the audit log keeps the events).
- New dependencies: `pyhanko`, `img2pdf`; `ocrmypdf` in the API image.

### Added - GED Phase 6 (lifecycle, trash, retention, legal hold, destruction)
- Document lifecycle: active → archived → to be destroyed → destroyed, enforced in one place (`lifecycle.py`). Archiving is done by an archivist (admin, or reviewer with the "archivist" permission); archived documents are read-only for everyone else.
- Trash: deleting a document (review page, upload / batch removal) now moves it to the trash - hidden everywhere, links kept, restorable for `TRASH_RETENTION_DAYS` (30) from the "Corbeille" page. After that the daily job destroys it with an automatically approved destruction record ("purge corbeille").
- Retention rules per series (covering sub-series) or per document type: duration in months from the document date (an extracted date field), filing, review or archiving date; outcome destroy, re-examine or keep forever. The most specific rule wins (nearest series, then type). Retention dates are recomputed when rules, series, filing or dates change; a daily job (local server) marks archived documents whose retention ended "to be destroyed" and notifies the archivists. An archivist can keep a document longer.
- Legal holds on a document, a series (and everything below it, including documents filed later) or the results of a search: held documents cannot be trashed, purged or destroyed until the hold is released (with a reason).
- Two-person destruction: an archivist proposes, another administrator approves (also enforced by the database). Approval destroys every listed document at once - files (all versions, page images, originals) deleted on both servers, extracted data, versions, history, tags and grants removed - and keeps a tombstone (record number, type, dates, SHA-256 fingerprints); links show the tombstone. A procès-verbal d'élimination (PDF, French + English for English-speaking organisations) is generated, mentioning that off-site backups keep the files until their retention expires.
- UI (fr/en): lifecycle badges on documents and in the list, lifecycle panel on the document (status, retention date and rule, holds, actions), "Corbeille", "Règles de conservation" (with legal holds) and "Éliminations" (to destroy, proposals, approval, PV download) pages, legal hold on search results, sidebar badge for archivists.
- Audit: `document_archived`, `document_trashed`, `document_restored`, `retention_rule_*`, `retention_extended`, `document_destruction_eligible`, `legal_hold_set` / `_released`, `destruction_proposed` / `_approved` / `_rejected` / `_cancelled`, `document_destroyed`, `destruction_pv_downloaded`.

### Changed - GED Phase 6
- No document is hard-deleted any more: the admin delete of a document and the ingest "remove upload" / "remove batch" routes trash instead (a batch with filed documents is hidden, not deleted); the review delete route now needs the archivist permission.
- Duplicate detection only considers live documents: the per-type `content_hash` unique index becomes partial (rebuilt once at startup), so a trashed or destroyed document never blocks uploading the same file again; restoring is refused while a live copy exists.
- The sync never transfers a destroyed file again and deletes the peer's copy (`sdai_destroyed_files`).
- New settings: `TRASH_RETENTION_DAYS`, `RETENTION_JOB_INTERVAL_HOURS`, `TRASH_PURGE_BATCH`, `DESTROYED_FILES_SWEEP_SECONDS`; optional organisation language (`sdai_tenants.locale`) for the PV.

### Added - GED Phase 5 (download, share links, selective export)
- "Download original": the untouched file of the current or any earlier version, named after the document's record id, every download recorded; optional watermark (who, when) on PDFs of confidential/secret documents (`DOWNLOAD_WATERMARK_CONFIDENTIAL`).
- External share links: expiry (1-30 days), optional password and download limit, a fixed version or always the latest, recipient note; the link is shown once and only its hash is stored; list and revoke from the document; outsiders get a minimal download page (`/s/...`, French/English), every use recorded with its IP. Secret documents can never be shared, confidential ones only by an administrator, and a link stops working as soon as its creator loses access.
- Selective export for every user: tick documents (or export all current results, or a series) → a ZIP with the originals (current or all versions), `metadata.json`, `metadata.csv`, a `manifest.sha256` to check integrity and a README in French and English; only documents the user may download; missing or altered files flagged; "My exports" page, ZIP kept 24 h. Limits: `EXPORT_MAX_DOCUMENTS`, `EXPORT_MAX_MB`.

### Changed - GED Phase 5
- nginx routes `/s/` to the API on every node (`PUBLIC_BASE_URL` is used to build share links).
- The public share-link routes are the only data routes without a signed-in user (CLAUDE.md).
- New dependency: `pypdf` (watermark).

### Added - GED Phase 4 (full-text search in page content)
- Every page is transcribed with Tesseract (`OCR_LANGS`, default French + English) in its own background lane, without slowing extraction; pages entered by hand are searchable by the values typed.
- `scripts/backfill_page_text.py` transcribes pages ingested earlier (resumable, throttled, local node only).
- Document search now looks in the extracted fields **and** the text of every page of the current version, ignores accents ("decret" finds "décret"), understands "phrases" and -exclusions, ranks field matches first, and returns highlighted snippets with the matching page numbers - still only over documents the user may read, trashed ones excluded.
- Search box at the top of the navigation on every page; results show snippets and "p. N" badges that open the document viewer at that page. French and English.
- Benchmark: 10 000 pages searched in ~60-110 ms per request.

### Changed - GED Phase 4
- Search and the per-type full-text indexes use a new `sdai_fr` configuration (French + `unaccent`, extension created at startup); indexes are rebuilt at the first start.
- Search results are ordered by relevance instead of date when a query is given.
- Generated columns are no longer sent by the local/cloud sync (each node computes them).

### Added - GED Phase 3 (classification)
- Hierarchical plan de classement: series with parent, classification code and order; tree view with document counts, create / rename / move (cycles refused) / delete-if-empty; breadcrumb on the document page.
- Series-level access grants now cover every sub-series.
- Document list and search filter by series "including sub-series" and by tag.
- Tags: tenant-wide vocabulary with colours, tagging by anyone who may edit the document (autocomplete, new names create tags), admin rename / merge / delete.
- Automatic filing: a series per document type; documents without a rule go to "À classer" / "To be filed".
- Link suggestions: documents cited by reference number (or citing this one) are suggested on the document page and linked in one click.
- Synchronised between local and cloud nodes; concurrent series moves on both nodes converge without cycles.
- Audit actions for series, tag and filing-rule changes. UI in French and English.

### Changed - GED Phase 3
- Series names are unique among siblings instead of across the whole tenant.
- Series endpoints moved to `classification_router.py` (same URLs; `GET /api/db/series` returns more fields).

### Added - GED Phase 8 (hybrid local/cloud deployment, sync, backups)
- Two-server deployment: the local server (GPU, works offline) stays primary and a cloud node (plain VM at DigitalOcean or AWS, no GPU, `docker-compose.cloud.yml`) holds the same data for remote users. Changes on either side are synchronised both ways whenever the link is up; days offline are fine.
- Change capture by database triggers on every synced table (incl. every per-type document table, group memberships, the audit log and the token blocklist) into `sdai_sync_changelog`, with hybrid-logical-clock stamps and per-field clocks; creating or extending a document type is replayed on the other node before its rows arrive.
- Node-to-node API `/api/sync/*` authenticated by HMAC-signed requests (shared `SYNC_NODE_TOKEN`, ±5 min, single-use nonce) - push, pull (gzip), resumable sha256-verified file transfer that never overwrites a file.
- `sync` service (`python -m sync.worker`): push/pull/file transfer every `SYNC_INTERVAL_SECONDS`, exponential backoff while offline, `SYNC_MAX_KBPS` bandwidth cap, changelog pruning.
- Field-level last-writer-wins; a field edited on both nodes is recorded as a sync conflict (both values, field history, audit `sync_conflict`), flagged in the review queue ("Conflit de synchronisation") and resolved by an admin (keep either value, type another, retry/dismiss failed changes - audit `sync_conflict_resolved`).
- Files referenced by synced rows (originals, page images, versions, path imports) are copied to the other node; until then the viewer shows "Fichier en cours de synchronisation".
- Cloud node mode: no pipeline on the cloud; documents uploaded there are processed by the local GPU once their file has arrived and the results (fields, record number) come back. Record numbers are only ever assigned on the local node.
- Restored or rebuilt nodes heal themselves: the sync detects that a node went back in time or starts empty and exchanges full snapshots merged on the original timestamps (nothing newer overwritten), then record numbering continues after the highest number in use. `python -m sync.join` prepares an empty new node.
- Off-site backups: `backup` service (restic) - nightly database dump + `/data` + `documents/` to an SFTP server (`BACKUP_SFTP_TARGET`) and optionally a NAS, encrypted (`RESTIC_PASSWORD`), 30 daily + 12 monthly; `restore-test` drill; RESTORE.md covers restoring a node on a clean VM and rebuilding either node from the other.
- Admin "Synchronisation" page (fr/en): node and peer status, pending changes/files/size, conflicts with resolve actions, last backups; alerts in the menu of every page when sync is down for `SYNC_ALERT_HOURS`, a backup failed or is stale, or files failed.
- Tests: mocked sync suites plus real-PostgreSQL capture, two-database convergence/conflict/file/restore suites and an end-to-end test with two API processes and the real worker.

### Changed - GED Phase 8
- `/api/ingest/pages/*` retry/reload/manual/skip/resume and review retry answer 409 on a cloud node (extraction and filing happen on the local node).
- The API and the sync worker serialise their startup migrations with an advisory lock.
- `api` Docker image now also copies `history.py` and `versions_router.py` (missing since Phase 2) and the `sync/` package.

### Added - GED Phase 2 (versioning, field history, tamper-evident audit)
- File versions per document (`sdai_document_versions`): `GET/POST /api/db/documents/{t}/{id}/versions`, `POST .../versions/{n}/restore` (a restore is a new version pointing at the old file - history is never rewritten), `GET .../versions/{n}/file`. Version 1 is backfilled for every existing document. New files are stored under `DATA_DIR/uploads/versions/<tenant>/<id>/v<N>_<name>` and never overwritten. Uploading/restoring needs `edit` and is refused while another user holds the check-out lock or, for non-archivists, on an archived document.
- Optional re-extraction of a new version into the same document (`reextract=true`): the pipeline updates the existing row instead of filing a new document; previous values go to the field history.
- Field history (`sdai_field_history`) with before/after values for field edits, review corrections, re-extraction and page re-merges, written in the same transaction as the change; `GET .../history` (field changes + document events). `document_fields_edited` and `document_approved` audit entries now include the old and new values.
- Audit events `document_viewed`, `document_downloaded` (version downloads and originals served by `/api/db/image`), deduplicated per user over 5 minutes; `document_version_added`, `document_version_restored`, `document_reextracted`, `integrity_check_completed`, `integrity_chain_broken`.
- Tamper-evident audit log: every entry is hash-chained per server (`origin_node`, `chain_seq`, `prev_hash`, `row_hash`) by a database trigger; `GET /api/admin/integrity/audit-chain` verifies it and reports the first edited, deleted or missing entry; existing entries are chained at startup. `GET /api/admin/integrity` returns the last completed check.
- The fixity check also verifies every version file; the scheduled check and `POST /api/admin/integrity-check` also verify the audit chain.
- UI (fr/en): Record / Versions / History tabs on the document page (upload a version, open, download, restore, field change timeline); integrity panel with last run, version counts and audit-chain status.
- Tests: `test_versions.py` (mocked) and 41 opt-in real-PostgreSQL tests in `test_versions_pg.py`.

### Changed - GED Phase 2
- `sdai_audit_log` is append-only (UPDATE/DELETE refused by a trigger) and `user_id` no longer has a foreign key (its `ON DELETE SET NULL` would have altered hashed entries).
- `/api/db/image` answers for original files are now audited downloads.

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
