# CONTEXT.md - Current Project State

This file captures what has been built, why, and what is known to be incomplete or pending. Update it as the project evolves.

---

## What Is Built

### Authentication

- JWT-based login via httpOnly cookie (`access_token=Bearer <token>`, `SameSite=Lax`).
- PostgreSQL-backed user management: `sdai_users` table with email, bcrypt password, role (`admin` | `reviewer` | `reader`), delegated flags, `clearance`, and `is_active` flag. See "Access Control" below.
- JWT includes a `jti` (UUID v4) for token blocklisting; logout writes `jti` to `sdai_token_blocklist`.
- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`.
- `CurrentUser` dataclass (`id`, `email`, `full_name`, `role`, `tenant_id`, `tenant_slug`, `tenant_name`, delegated flags, `group_ids`, `clearance`) returned by `get_current_user` dependency - the tenant fields come from a join to `sdai_tenants`, so a user always resolves to exactly one tenant.
- Role guards in `auth.py`: `require_admin` (admin is tenant-scoped, not global), `require_user` (any person - rejects API keys), `require_contributor` (upload/ingestion tracking - rejects readers), `require_reviewer` (document actions - rejects readers and API keys), `require_access_manager`, `require_extraction_editor`.
- `create_admin.py` seed script: `python create_admin.py --email admin@example.com --password secret --tenant default` (`--tenant` required; the tenant must already exist).
- React: `AuthContext` + `ProtectedRoute` + `AdminRoute` + `LoginPage`.
- All API data routes require a valid JWT - unauthenticated requests get HTTP 401.

### VLM Extraction Tab

- Reads `documents/ocr_results/json/vlm_qwen25_results.json` (output of `vlm_ollama_test.py`).
- Zod discriminated union on `extraction_confidence` → tier: `high | medium | low | failed`.
- Frontend renders `VlmPanel` - shows extracted fields keyed by confidence tier.
- Sidebar lists all filenames present in the VLM results JSON.

### OCR Comparison Tab

- Reads per-document JSON files from `documents/ocr_results/json/`.
- Shows side-by-side engine results (Tesseract, EasyOCR, etc.) via `OcrPanel`.
- Sidebar lists documents that have an OCR result file.

### Document Image Viewer

- Images served from `GET /images/raw/{filename}` and `GET /images/preprocessed/{filename}`.
- Rendered inside `OcrPanel` alongside the extracted text.

### Multi-Tenancy

- `sdai_tenants` table (`id`, `slug`, `name`, `prompt_file`, `list_fields`, `page_timeout_seconds`, `split_page_columns`, `is_active`). Every deployment auto-seeds a `default` tenant on startup, so a standalone/single-ministry deployment behaves exactly as before - tenancy only becomes visible once a second tenant is created.
- **Physical isolation, not row filtering**: each tenant's dynamically-created per-document-type tables are named `t_<tenant-slug>_<document_type>` (e.g. `t_land_titre_foncier`). `documents_router._get_tables_columns()` (the single discovery function nearly every endpoint routes through) filters by this prefix via `starts_with()`, so a missed filter returns nothing instead of leaking another tenant's rows.
- `sdai_users.tenant_id` and `batches.tenant_id` - every user belongs to exactly one tenant; `batch_documents`/`batch_document_pages` are *not* directly tenant-tagged (resolved via a JOIN through `batches` instead, to avoid duplicated/driftable columns).
- Per-tenant VLM config: `prompt_file` / `list_fields` / `page_timeout_seconds` / `split_page_columns` on `sdai_tenants` override the deployment-wide env vars (`VLM_PROMPT_FILE` etc.) for that tenant only, resolved via `ingest_router._get_tenant_config()` (30s in-process cache). NULL columns fall back to the env-var defaults - this is what makes the `default` tenant behave byte-identically to the pre-tenancy single-config deployment.
- Content-hash dedup and the ingest pipeline's Stage 1-3 (`_stage1_process`/`_stage2_worker`/`_stage3_process`) all thread `tenant_id`/`tenant_slug` end-to-end so a new document always lands in the uploader's own tenant's table.
- IDOR fix: `ingest_router.py`'s page/document/batch endpoints (previously reachable by any authenticated user via a guessed UUID, since they query `batches`/`batch_documents`/`batch_document_pages` directly rather than through the discovery layer) now check the resolved tenant against `current_user.tenant_id` and 404 on mismatch.
- Cache isolation: `cache._path_key_builder` keys on tenant **and user** (`<ns>:<tenant>:<user_id>:<path>`) - every cached endpoint (`/api/db/types`, `/reviewers`, `/schema`, `/api/review/count`) answers with what the caller may see, so neither another tenant nor another user of the same tenant can be served someone else's counts.
- Provisioning is CLI-only: `create_tenant.py --slug <slug> --name <name> [--prompt-file ...] [--list-fields ...] [--page-timeout-seconds ...] [--split-page-columns]`, then `create_admin.py --tenant <slug>`. No in-app "create tenant" UI in this version. `default` is a reserved slug.

### Generalized Search

- Full-text search and the document list's displayed fields are **not** hardcoded to the admin-document field names anymore (they were until this was built - see the now-resolved "Full-text search on custom schemas" entry that used to be in Known Limitations below). `documents_router._get_tables_columns()` returns `{table: {col: data_type}}` instead of `{table: {col, ...}}`; `_searchable_cols()`/`_extra_cols()` (documents_router.py) classify columns as "TEXT and not a system column" (searchable) vs. "not a system column, not document_type" (goes into the `extra_fields` JSONB blob every list/queue row now carries instead of 5 fixed named columns).
- `ingest_router._ensure_table()`'s GIN FTS index is built over every TEXT-typed VLM-extracted field at ingest time (`_BASE_COLS` frozenset excludes system columns), not a fixed 4-field tuple - mirrored exactly at query time so the WHERE clause always matches the index expression. A one-time startup migration, `_backfill_fts_indexes()` (called from `_init_db()`), rebuilds every existing table's index under this rule so pre-existing tenants/tables aren't stuck with the old narrower index.
- Frontend: `DbDocumentSchema`/`ReviewQueueItemSchema` (`packages/types/src/documents_db.ts`/`review.ts`) carry `extra_fields: Record<string, unknown>` instead of the 5 removed fields. `DocumentsPage.tsx`'s result table shows a single "Details" column summarizing `extra_fields` (`summarizeExtra()` in `apps/frontend/src/utils/format.ts`) instead of 3 fixed columns. `ReviewPage.tsx`/`ReviewForm` needed no changes - they already operated on the fully-dynamic per-document detail fetch, not the queue-item list shape.

### Document Links (chain-of-custody)

- `sdai_document_links` table (control-plane, not tenant-table-prefixed - `tenant_id` is a real column, like `batches`/`sdai_users`): `from_table`/`from_id` → `to_table`/`to_id`, a free-text `relation` (UI suggests "concerns"/"supersedes"/"transfers"/"amends"/"renews" but doesn't enforce a fixed vocabulary), optional `note`, `created_by`. Unique index on `(tenant_id, from_table, from_id, to_table, to_id, relation)` makes duplicate link requests idempotent (`ON CONFLICT DO NOTHING` + fallback SELECT returns the existing row rather than erroring).
- Endpoints in `documents_router.py`: `GET/POST /api/db/documents/{table}/{id}/links`, `DELETE /api/db/links/{link_id}`. Both `from_table`/`to_table` must be in the caller's own tenant's `_get_tables_columns()` result - this app-layer check is the entire tenant-isolation boundary for this feature, since a real FK against a dynamically-named table isn't possible. `DELETE` is `get_current_user`-level (any tenant user), not admin-only - removing a link doesn't destroy extracted data.
- Cascade-delete: hard-deleting a document (`review_router.delete_document`, admin-only; `ingest_router._delete_document_by_batch_document_id`, the re-upload/dedup-replace path) also deletes any links referencing it, via a shared `documents_router._delete_links_for_document()` helper (imported into `ingest_router.py` via a lazy import to avoid a circular dependency, same pattern already used for `auth`/`audit`'s use of `ingest_router._engine`), in the same transaction as the document delete.
- Frontend: `LinksSection.tsx` (mounted on `DocumentDetailPage.tsx`, below the field list) shows "Relates to"/"Referenced by" groups; `DocumentLinkPicker.tsx` is a from-scratch searchable picker (no reusable autocomplete component existed before this) reusing the generalized-search `fetchDbDocuments({q})` call.
- **Known gotcha already fixed once**: `_INIT_DDL` is naively split on `;` by `_init_db()` - a semicolon inside a `--` SQL *comment* (not just inside a `DO $$` block, the previously-known gotcha from the multi-tenancy migration) silently breaks this and produces a confusing SQLAlchemy/asyncpg `TypeError` on startup, not a clear SQL error. Hit and fixed during this feature's development; no semicolon characters are allowed in any comment line inside the `_INIT_DDL` string.

### GED Foundation (Phase 0)

- Every per-type table carries the GED document-level columns listed in `ingest_router._GED_COLS` (lifecycle, confidentiality, original file + hash, version, trash, retention, legal hold, lock, sync). Added at CREATE time by `_ensure_table` and to existing tables at startup by `_backfill_ged_columns`; all are in both `_BASE_COLS` sets (never searchable/extra fields) and in `_PROTECTED_FIELDS` (never editable via field edits). Most are inert until later GED phases.
- `original_path` / `original_hash` = the untouched uploaded file (`batch_documents.source_path`) and its `content_hash`, recorded by `_store_extraction` via `_original_upload_path()`. Never a path under `IMAGES_DIR` (a crashed-document retry registers a preprocessed page image as `source_path`, which would never hash back). The fixity check re-hashes it - image-sourced documents now get a real corruption check.
- `_access_where_clause(table, user, include_deleted=False)` always includes `_live_where_clause(table)` (hides `deleted_at IS NOT NULL` and `lifecycle_status = 'destroyed'`), **including for admins** - so it is no longer `""` for admins unless `include_deleted=True`.
- Sync groundwork: `sdai_touch_row()` trigger (`_TOUCH_ROW_FN_DDL`) sets `updated_at` and `origin_node` (from the `sdai.node_id` setting) on every row of the dynamic tables and of `_SYNCED_STATIC_TABLES`; skipped when `sdai.applying_sync = on`. `sdai.node_id` is set per pooled connection (`_set_node_id_on_connect`) and as a database default (`_register_node`, needs DB ownership - warns otherwise). `hlc` stays NULL until sync exists.
- Node identity: `NODE_ID` (validated `[a-z0-9][a-z0-9_-]{0,39}`) / `NODE_ROLE` (`local`|`cloud`), `sdai_nodes` (node-local, not synced), returned by `/api/auth/me` and login, shown as a sidebar badge.

### Access Control (GED Phase 1)

- **Roles**: `admin` (sees and does everything in its tenant), `reviewer`, `reader` (read-only: browses, searches, downloads what it can see - never uploads, reviews, edits, files or links; kept off `/upload` and `/review` by `ContributorRoute`), and the synthetic `api_key` (upload + ingestion status only - no archive access, no page actions).
- **Delegated flags** on `sdai_users`: `can_manage_access` (document grants + confidentiality), `can_edit_extraction` (field edits), `can_manage_lifecycle` (archivist actions, used from GED Phase 6), `can_share` (share links, GED Phase 5). They only take effect for reviewers - a reader stays read-only - except `can_share`. Effective values: `CurrentUser.can_manage_document_access` / `can_edit_extraction_data` / `can_manage_document_lifecycle` / `can_share_documents`.
- **Grants** (`sdai_document_access`): `scope` = `document` (table_name + document_id), `series` (series_id - every document filed in it) or `doc_type` (table_name - every document of that type); `permission` = `read` < `download` < `edit` (cumulative). A document that no grant targets at any scope is open to its whole tenant; as soon as one does, only admins and holders of a targeting grant see it, at the level that grant gives (union of all targeting grants). Uniqueness is per scope (partial unique indexes); grants that predate scopes were migrated to `document`/`edit` (they used to mean full access within the role), new grants default to `read`, the uploader auto-grant is `edit`.
- **Confidentiality / clearance**: each document has `confidentiality` (`public` < `internal` < `confidential` < `secret`, default `internal`); each user has `clearance` (default `internal`). A document above the user's clearance is invisible whatever the grants; admins bypass. `PATCH /api/db/documents/{t}/{id}/confidentiality` (access managers; a non-admin can't classify above their own clearance), audited as `confidentiality_changed`.
- **Role caps**: reader ≤ `download`, reviewer ≤ `edit`. Levels per action: `read` = list/search/detail/links/images/pages; `download` = per-document export (original download in Phase 5); `edit` = field edits (plus `can_edit_extraction`), approve/reject/flag/retry, series assignment, link create/delete, page retry/reload/skip/cancel/manual/pause/resume. The review queue and count list only documents the user can `edit` (so always empty for readers).
- **SQL**: `documents_router._access_where_clause(table, user, permission="read", include_deleted=False)` - one boolean expression (EXISTS sub-queries over the three scopes, tenant-filtered, plus the live filter and the clearance check); `"FALSE"` when the role can never reach the level. `_document_visible(..., permission)`, `_require_document_permission` (404 if invisible, 403 if visible below the needed level), `_effective_permission` (returned as `my_permission` by `GET .../access` so the UI hides what the backend would refuse - `useDocumentPermissions`).
- **Grant endpoints**: document grants `POST/GET /api/db/documents/{t}/{id}/access` (listing includes inherited series/type grants), `PATCH /api/db/access/{id}` (level), `DELETE /api/db/access/{id}`; series/type grants `GET/POST /api/db/grants` (admin-only). Users: `PATCH /api/admin/users/{id}` sets `role` (reviewer/reader only - admins are never demoted there), flags and `clearance`, audited as `user_permissions_changed`.
- **Files and pipeline**: `GET /api/db/image?path=...[&table=&id=]` - with `table`+`id` (what the frontend sends) the named document must be readable and reference the file; otherwise the file is traced to its upload(s) in the caller's tenant: once filed, the filed document decides; still in the pipeline, contributors only; unknown to any upload, only through a readable document referencing it; anything else 404. Ingest pages and page actions follow the same rule via `_filed_document_permitted` (read for `GET /pages/{bd}`, edit for actions).
- **Other leak fixes**: links to a document the caller can't see come back anonymous (no id, table, title, type or note); `/api/admin/audit-log` only shows the admin's own tenant (actions by its users or on its tables).
- **Tests**: `tests/test_permissions.py` (mocked role/flag matrix, clause structure, endpoint levels) and `tests/test_access_pg.py` (23 tests against a real PostgreSQL: listing, search, counts, queue, links, export, images, pages, scoped grants, confidentiality, constraints - opt-in, `TEST_DATABASE_URL=... pytest -m pg`, wipes that database).

### Versioning, Field History and Tamper-Evident Audit (GED Phase 2)

- **File versions** (`sdai_document_versions`, one row per version, never updated or deleted): version 1 is the ingested file - the untouched original, else the original PDF, else the (preprocessed) primary image (no hash then) - written by `_store_extraction` for new documents and backfilled at startup by `_backfill_document_versions`. The document's `current_version` names the live one. Routes in `versions_router.py`: `GET /api/db/documents/{t}/{id}/versions` (read), `POST .../versions` (multipart `file`, `comment`, `reextract`; reviewer/admin with `edit`) stores the file exclusively at `DATA_DIR/uploads/versions/<tenant>/<id>/v<N>_<name>`, `POST .../versions/{n}/restore` (edit) adds a NEW version pointing at version n's file (`restored_from`), `GET .../versions/{n}/file[?inline=true]` (download). Uploading/restoring is refused (409) while someone else holds the check-out lock (`locked_by`/`locked_until`) and (403) on an `archived` document unless the caller manages lifecycle.
- **Re-extraction**: with `reextract=true` the version's file runs through the normal pipeline as its own upload (batch `source_type='version'`); its `batch_documents` row carries `target_table` / `target_document_id` / `target_version`, so Stage 3 (`_stage3_reextract`) and the per-page merge call `_apply_reextraction`, which updates the SAME document row (fields, confidence, review status, page images, PDF) instead of filing a new one. `document_type` never changes (the row stays in its table; a different detected type is noted in the audit details). A failed run leaves the document untouched. The version upload's pages/images follow the target document's access (`documents_router._upload_match`).
- **Field history** (`sdai_field_history`: field, old/new value as JSONB, `changed_by`, `change_source` `edit|review|reextract|sync`, `version_no`) written by `history.record_field_changes` in the same transaction as the change: field edits (`edit`), approve with corrections (`review`), version re-extraction and page retry/manual-entry re-merges of an already-filed document (`reextract`, no user for the latter). Only real changes are recorded (`history.field_changes`: "" and NULL are the same, a JSON string equals the same list). `document_fields_edited` / `document_approved` audit details carry `changes: {field: {old, new}}`. `GET .../history` returns field changes + the document's audit events (no IPs; `document_viewed`/`document_downloaded` only for access managers).
- **Access audit**: `document_viewed` on the detail endpoint, `document_downloaded` on a version download and on `/api/db/image` of any file outside `DATA_DIR/images` (an original), attributed to the named document only when it really references the file. Both are deduplicated per user and document/file over 5 minutes (`cache.first_in_window`: Redis `SET NX EX`, per-process fallback). Also `document_version_added`, `document_version_restored`, `document_reextracted`.
- **Hash-chained audit log**: `sdai_audit_log` gains `origin_node`, `chain_seq`, `prev_hash`, `row_hash`. The `sdai_audit_chain` BEFORE INSERT trigger (in the database, so `log_action()` is still one INSERT that never raises) takes a per-node advisory lock and sets `chain_seq` = previous + 1, `prev_hash`, and `row_hash = sdai_audit_row_hash(...)` (sha256 over the row's canonical jsonb, created_at as UTC ISO). One chain per node (`NODE_ID`), so the local and cloud logs never fork. `sdai_audit_append_only` refuses UPDATE/DELETE (except chaining legacy rows); the `user_id` FK was dropped (its ON DELETE SET NULL would have rewritten hashed rows). Legacy rows are chained at startup (`_backfill_audit_chain`). `_verify_audit_chain` reports the first break (`missing_rows`, `prev_hash_mismatch`, `row_altered`, `truncated`, `head_rewritten`, `unchained`) and records each chain's verified head in `sdai_audit_chain_heads`, so later removal of the newest rows or a recomputed chain is caught too.
- **Integrity**: the fixity check also verifies every file in `sdai_document_versions` (each distinct file once, `versions_checked`/`versions_ok`), and the integrity worker / `POST /api/admin/integrity-check` verify the audit chain (logging `integrity_chain_broken`) and record `integrity_check_completed` per tenant. `GET /api/admin/integrity` = last completed check of the tenant, `GET /api/admin/integrity/audit-chain` = verify now.
- **UI**: `DocumentDetailPage` right pane has tabs Record / Versions / History (`VersionsSection`, `HistorySection`, `hooks/useVersions.ts`); the integrity panel on `AccessAdminPage` shows the last run, version counts and the chain verdict with a "Verify audit chain" button; new actions in `AuditPage`. fr/en.
- **Tests**: `tests/test_versions.py` (mocked: Stage 3 routing, version fixity, diff, filename sanitizing) and `tests/test_versions_pg.py` (real PostgreSQL, opt-in `-m pg`: backfill, upload/restore/download, lock/archive refusals, re-extraction into the same row, history on edit/review/re-extract/re-merge, view/download audit, chain under concurrency, edited/deleted/truncated rows, legacy chaining, fixity on versions).

### Local/Cloud Sync and Backups (GED Phase 8)

Two servers hold the same deployment: the **local** node (on site, GPU, works offline, drives the
sync) and a **cloud** node (plain VM at DigitalOcean/AWS, no Ollama, always reachable). Code in
`apps/api/sync/`.

- **Change capture** (`sync/capture.py`): an AFTER trigger `sdai_capture_change` on every synced table
  (`SYNC_TABLES`, in FK order, incl. composite-key `sdai_user_groups` and insert-only
  `sdai_audit_log` / `sdai_token_blocklist`; every per-type table via `_ensure_table`) writes
  `sdai_sync_changelog` rows: insert = whole row, update = changed columns + `base_hlcs` (the field
  clocks it overwrote), delete, and `ddl` events (column list of a new/altered per-type table, emitted
  by `_ensure_table`) so the peer creates tables before rows arrive. Stamps are hybrid logical clocks
  made in SQL (`sdai_hlc_now()`: max(wall clock, `sdai_sync_state.hlc_floor_ms`) + a sequence counter +
  node id, text-sortable - `sync/hlc.py` documents the format); `sdai_touch_row` also stamps the row's
  `hlc`. `sdai_sync_row_clock` keeps the last stamp of each field of each row. Writes applied FROM the
  peer run with `sdai.applying_sync = on` and are not captured (no echo). The changelog is seeded once
  with every existing row (`seed_changelog`, also on demand = "reseed", new `epoch`). Node-local, never
  synced: `sdai_nodes`, `sdai_record_id_counters`, `sdai_audit_chain_heads`, `sdai_sync_*`,
  `sdai_backup_runs`.
- **Apply** (`sync/apply.py`): in one transaction, a savepoint per change. Identifiers are checked
  against synced tables / existing tenants' per-type tables and existing columns; values are typed
  through `jsonb_populate_record`. Field-level last-writer-wins on the field clocks: an update whose
  base differs from the local clock was concurrent - if the values differ it is a **conflict**. A
  delete wins. A change that can't be applied (unique key taken, missing parent, unknown
  table/column) is reported, never stalls the sync. Afterwards the HLC floor is raised past the
  peer's stamps.
- **Node-to-node API** (`sync/api.py`, `/api/sync/*`, 404 without `SYNC_NODE_TOKEN`): HMAC-SHA256
  signed requests (`sync/auth.py`: node, timestamp ±5 min, single-use nonce, body hash), never user
  cookies or API keys; `hello`, `push` (gzip, applied exactly once per peer cursor), `pull` (gzip),
  `files/info`, `files` GET/PUT (chunked, resumable `.sync-part`, sha256-verified, `link()` so a file
  is never replaced; paths only inside the file roots). Rate limit 1200/min.
- **Worker** (`sync/worker.py`, compose service `sync`): on the node with `SYNC_PEER_URL` (the local
  one - the cloud can't reach a server behind NAT, so the cloud only answers), every
  `SYNC_INTERVAL_SECONDS`: hello → push pages → pull pages → file transfers; exponential backoff to 15
  min while offline, `SYNC_MAX_KBPS` throttle, caches invalidated after applying, status in
  `sdai_sync_state` / `sdai_sync_cursors`. Hourly pruning of changelog rows every peer applied
  (older than `SYNC_CHANGELOG_RETENTION_DAYS`).
- **Cursors and epochs** (`sync/store.py`): per peer, `received_id` (its changelog applied here) and
  `sent_id` (ours applied there), tied to the changelog `epoch`. `history_diverged` catches a node
  restored from a backup or rebuilt (one side holds more than the other has, or acknowledges less than
  before, or an unknown epoch) and answers with a reseed: a full snapshot merged field by field on the
  original stamps, so nothing newer is overwritten and rows only the other side still holds come back
  (`forget_peer` asks the peer for its snapshot too). `_repair_record_counters` then raises the
  `record_id` counters past every number in use.
- **Conflicts** (`sync/conflicts.py`, `sdai_sync_conflicts`, node-local): one open row per field, both
  values, winner, a `sdai_field_history` entry (`change_source='sync'`), audit `sync_conflict` (after
  commit, so it is captured and the peer's copy of the audit chain has no gap); the document shows in
  the review queue (`review_router._queue_where`, `sync_conflict` flag). `/api/admin/sync/conflicts` +
  `/resolve` (keep_local / keep_remote / manual / retry / dismiss, admin, own tenant + tenantless rows);
  a resolution is an ordinary edit, which closes the peer's copy ("superseded") when it arrives.
- **Files** (`sync/files.py`, `sdai_sync_file_queue`, node-local): every file named by a pushed/pulled
  change (`FILE_COLUMNS`: source_image_path, source_pdf_path, original_path, source_path, image_path,
  file_path, page_image_paths) is queued `push` or `fetch` and transferred by the worker (bounded per
  cycle, waiting files retried with backoff). A referenced file not there yet answers 404 with
  `X-Sync-Pending` and the message "Fichier en cours de synchronisation" (`/api/db/image`, version
  download, the image viewer).
- **Cloud node mode** (`NODE_ROLE=cloud`, `docker-compose.cloud.yml`: no Ollama, ports 80/443): no
  pipeline workers, uploads stay `pending` (`ingest_router._submit`), the local node picks up
  peer-origin pending uploads once their file has arrived (`_peer_upload_pickup_worker`) and the
  results sync back; `record_id` is only assigned on the local node (`_next_record_id` raises
  elsewhere); page retry/reload/manual/skip/resume and review retry answer 409 there. A new cloud
  node is prepared with `python -m sync.join` (empty database only; `join_pending` stops startup from
  re-creating a `default` tenant until the peer's data arrives).
- **Backups** (`scripts/backup/`, compose service `backup`): nightly `pg_dump` + `/data` +
  `documents/` with restic to `BACKUP_SFTP_TARGET` (+ `BACKUP_NAS_PATH`), encrypted with
  `RESTIC_PASSWORD`, 30 daily / 12 monthly, each run recorded in `sdai_backup_runs`; `restore-test`
  drill. Procedures in RESTORE.md.
- **Admin page** `/ops/sync` (`SyncAdminPage`, fr/en): node, peers, pending changes/files/MB,
  conflicts with resolve actions, backups; `GET /api/admin/sync/status` computes alerts (sync down
  > `SYNC_ALERT_HOURS`, backup failed/stale/not configured, files failed, conflicts open), shown as a
  badge + alert box in the sidebar of every page for admins.
- **Tests**: `test_sync_hlc.py`, `test_sync_api.py`, `test_sync_worker.py` (mocked);
  `test_sync_pg.py` (capture), `test_sync_twonode_pg.py` (two databases: first sync, no echo, merges,
  conflicts and their resolution, ddl replay for a new tenant/type, deletes, bad changes, files, cloud
  upload → local processing → back, restore/rebuild of either node, status API) and
  `test_sync_e2e_pg.py` (two real API processes + the real worker over HTTP, edits on both sides while
  disconnected, convergence), all real-PostgreSQL ones opt-in with `-m pg`.

### Document Upload & Ingestion

- `POST /api/ingest` - accepts multipart file upload, stores document in PostgreSQL.
- Dynamic schema management: `_ensure_table()` creates or alters tables based on extracted fields.
- Extracted fields stored in per-type tables (e.g., `doc_invoice`, `doc_permit`).
- `UploadPage` at `/upload` with drag-and-drop file selector.
- **Content-hash deduplication**: SHA-256 hash computed before preprocessing. If an identical file was previously ingested, the pipeline is skipped and the document is recorded with `status='duplicate'`. The `GET /api/ingest/status/{batch_id}` response includes `duplicates_skipped` count. The Upload page shows a violet duplicate badge and counter.
- All per-type document tables have a `content_hash TEXT` column with a unique index (`idx_{table_name}_content_hash`) to enforce deduplication at the database layer.

### Document Database Browser

- `GET /api/documents/types` - returns list of document tables with record counts.
- `GET /api/documents/{table}` - paginated list of records in a given table.
- `GET /api/documents/{table}/{id}` - single document detail.
- `DocumentsPage` at `/documents` - searchable/filterable list with counts per type.
- `DocumentDetailPage` at `/documents/:tableName/:id` - full field display.

### Schema Inspector

- `GET /api/schema` - returns all per-type table schemas (columns, types).
- `SchemaPage` at `/schema` - table and column listing.

### Review Queue

- `PATCH /api/review/{table}/{id}` - approve (with optional field edits) or reject a document.
- `POST /api/review/{table}/{id}/flag` - flag a document for manual attention.
- `reviewed_by` column updated on approval/rejection.
- `ReviewPage` at `/review` - lists documents pending review, with approve/reject/flag actions.
- Review count badge in dashboard sidebar (red badge when items are pending).

### Audit Log

- `sdai_audit_log` PostgreSQL table: `id`, `user_id`, `user_email`, `action`, `table_name`, `document_id`, `details` (JSONB), `ip_address`, `created_at`, plus the hash-chain columns `origin_node`, `chain_seq`, `prev_hash`, `row_hash` (append-only, see "Versioning, Field History and Tamper-Evident Audit").
- 8 action types logged automatically: `document_ingested`, `document_approved`, `document_rejected`, `document_flagged`, `user_login`, `user_logout`, `schema_created`, `schema_altered`.
- `audit.py` helper: `log_action()` never raises - failures are swallowed to stderr, never affecting callers.
- `GET /api/admin/audit-log` - admin-only endpoint with pagination (`page`, `page_size`) and filters (`action`, `date_from`, `date_to`, `user_id`). Tenant-scoped: an admin only sees entries by its tenant's users or on its tenant's tables.
- `AuditPage` at `/admin/audit` (admin-only, guarded by `AdminRoute`):
  - Action badge with color coding per action type.
  - Date range and action type filters.
  - Client-side CSV export (all items on current page).
  - Sticky table header, UUID truncation, IP address column.
  - Pagination footer.

### Offline Deployment

- `setup.sh` - run on an internet-connected machine to prepare `./models/` bundle:
  - Exports Ollama model data via Docker volume as `ollama-models.tar.gz`.
  - Saves Docker images as tarballs.
  - Downloads Python wheels and pnpm packages locally.
- `install.sh` - run on the offline server:
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
- `Dockerfile.frontend`: multi-stage - pnpm build → nginx. nginx proxies `/api/` and `/images/` to the API container (no CORS needed).
- `docker-compose.yml`: `api`, `frontend`, `ollama`, `db`, `redis`, `sync` (sync worker) and `backup` (restic) services on shared network; `docker-compose.cloud.yml` turns a host into the cloud node (no Ollama, ports 80/443).

---

## Architecture Decisions

| Decision | Reason |
|---|---|
| httpOnly cookie (not localStorage JWT) | Prevents XSS token theft |
| JWT `jti` blocklist in PostgreSQL | Allows immediate invalidation on logout |
| CORS disabled in Docker | nginx proxies internally - no cross-origin request |
| Zod discriminated union on `tier` | Guarantees exhaustive handling of confidence tiers in TypeScript |
| packages export `./src/index.ts` directly | No build step needed for workspace packages; Vite resolves them at dev time |
| `moduleResolution: "bundler"` | Required for Vite + TypeScript path aliases to resolve workspace packages correctly |
| `documents/` as a volume | Documents are not part of the source code; avoids bloating Docker images |
| Lazy imports in `auth.py` and `audit.py` | Breaks circular import: `ingest_router → auth → ingest_router` |
| `log_action()` swallows exceptions | Audit failures must never disrupt the calling endpoint's response |
| `_ensure_table()` returns `"created"/"altered"/"unchanged"` | Lets `_store_extraction()` fire schema audit events without duplicating the table check |
| Docker volume export for offline Ollama | `ollama save/load` don't exist - volume tarball is the only portable approach |
| SHA-256 dedup checked before preprocessing | Avoids VLM cost on duplicates; the hash is cheap relative to Ollama inference |
| `proxy_cache_path` before `server {}` in nginx conf | nginx includes these files inside its `http {}` block, so the directive is at the correct context level |
| `@pytest.mark.benchmark` for perf tests | Keeps the standard CI run fast; benchmarks opt-in with `pytest -m benchmark` |
| Tenant-prefixed tables, not a shared table + `tenant_id` column | Physical isolation - a missed `WHERE` clause returns nothing instead of leaking another ministry's rows; matches the "internal government archive" sensitivity level |
| Tenant/user provisioning is CLI-only (no in-app UI) | Keeps v1 scope controlled; onboarding a new ministry is a deliberate ops action, same pattern as `create_admin.py` already used |
| Per-tenant VLM config falls back to env vars when unset | Makes the auto-seeded `default` tenant byte-identical to the pre-tenancy single-config deployment - no behavior change for standalone deployments |

---

## Known Limitations / Not Yet Built

| Feature | Status |
|---|---|
| Document search / filter by field values | Not built - document list is unfiltered |
| VLM model selector | Not built - only `vlm_qwen25_results.json` is wired to the VLM tab |
| Field correction UI | Not built - document detail view is read-only |
| Mobile / responsive layout | Not tested - designed for desktop |
| Email notifications | Not built - review queue is pull-only |
| Bulk review actions | Not built - documents are approved/rejected one at a time |
| Audit log user filter in UI | Backend supports `user_id` filter but UI exposes only `action` and date range |
| Tenant deactivate/delete flow | `sdai_tenants.is_active` exists but nothing sets it to `false` - retiring a tenant currently means a manual SQL `UPDATE` |
| Index names on long table names | `idx_<table>_<col>` can exceed Postgres's 63-char limit and get truncated - on a very long `t_<tenant>_<type>` name two GED indexes may collide and one is silently skipped (same pre-existing pattern as `series_id`/`uploaded_by` indexes) |
| Trashed rows still count as duplicates | `_find_duplicate` and the admin full export don't apply the live filter yet - decided in GED Phase 6 |
| Batch list / status visible tenant-wide | `GET /api/ingest/batches` and `/status/{batch_id}` still show every contributor of the tenant all batches (counts, filenames, document types) - the pipeline is shared by design; page contents, images and actions do follow the filed document's access |
| API keys can no longer steer page extraction | Since GED Phase 1 an API key reaches only upload, presets, batches, status and page listing - retry/reload/skip/cancel/pause/resume need a reviewer |
| Version files outside the viewer | A new version without re-extraction is stored and downloadable from the Versions tab, but the page viewer keeps showing the pages of the last extracted file |
| Re-extraction keeps the document type | A version whose extraction detects another document type stays in its table; the detected type is only noted in the `document_reextracted` audit entry |
| Hard deletes vs. versions/history | Admin hard delete and re-upload replace leave `sdai_document_versions` / `sdai_field_history` rows (and files) behind - GED Phase 6 replaces hard deletes with trash/destruction |
| Audit chain vs. a DB superuser | The chain shows edits, deletions and truncation, but someone able to rewrite both the log and `sdai_audit_chain_heads` could recompute it undetected - the replicated copy on the other node (GED Phase 8) is the external witness |
| Sync topology | Exactly one peer per node (`SYNC_PEER_URL`), no relaying - a third node would need its own pairing; the local node must be able to reach the cloud (not the reverse) |
| Sync and file deletion | Deleting a row deletes it on the peer, but files are never deleted by sync (nor anywhere yet - GED Phase 6) |
| Unique keys created on both sides | Two different rows with the same natural key created offline on both nodes (e.g. the same e-mail for two new users) can't both exist: the second arrives as a `unique_violation` conflict to settle by hand |
| Cache invalidation across processes | The sync worker clears the API's caches through Redis; without Redis (in-memory cache) cached counts/schema on the API side refresh only when they expire |
| HLC counter wrap | Two changes of one node in the same millisecond after 100 000 others (counter wrap) could be ordered wrongly - not reachable in practice |
| Hourly-scale clock skew | Sync requests are refused beyond ±5 min skew - both nodes must run NTP |
| Conflicts per node | Each node keeps its own conflict list; resolving on one closes the other's copy only when the resolution reaches it (next cycle) |
| UI not clicked through | The Synchronisation page and badges were type-checked and built, not tested in a browser |
| Audit entries of API keys | API-key actions carry no `sdai_users` id, so the tenant-scoped audit view only shows them when they name one of the tenant's tables |

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
  admin_router.py                   # /api/admin/* - audit log, users/groups, integrity check + status, export
  versions_router.py                # /api/db/documents/{t}/{id}/versions|history - file versions, restore, history
  history.py                        # field-history diff + insert (sdai_field_history), shared by every edit path
  sync/                             # local/cloud sync: capture, hlc, apply, store, conflicts, files, api, admin, worker, join
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
    test_permissions.py             # GED Phase 1 role/flag/permission-level matrix + leak tests (mocked)
    test_access_pg.py               # GED Phase 1 access control against a real PostgreSQL (opt-in, -m pg)
    test_versions.py                # GED Phase 2 versioning/fixity/history helpers (mocked)
    test_versions_pg.py             # GED Phase 2 versions, history, audit chain on a real PostgreSQL (opt-in, -m pg)

apps/frontend/src/
  context/AuthContext.tsx           # global auth state (user, login, logout)
  components/auth/ProtectedRoute.tsx
  components/auth/AdminRoute.tsx    # redirects non-admins to /
  components/auth/ContributorRoute.tsx # keeps readers off /upload and /review
  components/AccessSection.tsx      # grants (own + inherited), levels, confidentiality, "your access"
  components/ConfidentialityBadge.tsx
  utils/permissions.ts              # UI mirror of role/flag/level rules (hide what the backend refuses)
  pages/LoginPage.tsx
  pages/DashboardPage.tsx           # main app shell - sidebar + tab switcher + panels
  pages/UploadPage.tsx              # file upload UI
  pages/DocumentsPage.tsx           # database document browser
  pages/DocumentDetailPage.tsx      # single document - tabs Record / Versions / History
  pages/SchemaPage.tsx              # schema inspector
  pages/ReviewPage.tsx              # review queue
  pages/AuditPage.tsx               # audit log (admin only)
  components/DocumentList.tsx
  components/OcrPanel.tsx
  components/VlmPanel.tsx
  components/LinksSection.tsx       # "Related documents" panel on DocumentDetailPage
  components/DocumentLinkPicker.tsx # searchable picker for creating a new link
  components/VersionsSection.tsx    # "Versions" tab: list, open/download, restore, upload (+ re-extract)
  components/HistorySection.tsx     # "History" tab: field before/after timeline + document events
  hooks/useVersions.ts              # versions/history queries, upload/restore mutations
  hooks/useDocuments.ts             # TanStack Query hooks for OCR
  hooks/useVlm.ts                   # TanStack Query hooks for VLM
  hooks/useDocumentsDb.ts           # hooks for database document browser
  hooks/useDocumentLinks.ts         # hooks for chain-of-custody links
  hooks/useIngest.ts                # upload mutation hook
  hooks/useReview.ts                # review actions + count hook
  hooks/useSchema.ts                # schema query hook
  hooks/useAudit.ts                 # audit log query hook
  utils/format.ts                   # labelFor(), summarizeExtra() - shared across pages/components

packages/types/src/
  auth.ts                           # UserSchema (roles, flags, clearance), CONFIDENTIALITY_LEVELS
  ocr.ts                            # OcrDocument, DocumentEntry, EngineResult schemas
  vlm.ts                            # ExtractionResult discriminated union + parseVlmResult()
  documents_db.ts                   # DbDocument (with extra_fields), DbType schemas
  ingest.ts                         # IngestResult schema
  review.ts                         # ReviewQueueItem (with extra_fields), ReviewPatch schemas
  schema.ts                         # SchemaInfo schemas
  audit.ts                          # AuditLogEntry, AuditLogPage schemas
  links.ts                          # DocumentLink, CreateLinkBody schemas
  access.ts                         # groups, admin users, grants (scope + permission level), integrity results
  versions.ts                       # document versions, field history, audit-chain verification

packages/api-client/src/
  index.ts                          # all fetch functions, imageUrl helper, fetchAuditLog()

setup.sh                            # internet-connected machine: prepare offline bundle
install.sh                          # offline server: install from bundle + docker compose up
```

---

## Suggested Next Steps

> **Active roadmap: GED extension** - see README.md, "Document management (GED)" > Roadmap.
> It builds on what is listed above (tenants, access grants, series, links, record_id, exports, i18n).

1. **Audit log user filter** - expose the `user_id` filter in `AuditPage` with a user email autocomplete.
2. **VLM model switcher** - allow selecting between multiple `vlm_*_results.json` files via a dropdown.
3. **Bulk review** - select multiple documents in `ReviewPage` and approve/reject in batch.
4. **Field correction** - allow users to edit extracted fields in `DocumentDetailPage` and save to the database.
5. **Email notifications** - notify reviewers when new documents are ingested and pending review.
6. **Auto-suggest document links** - when a VLM-extracted field (e.g. `reference_number`) matches an existing record, suggest a link during review instead of requiring the reviewer to search for it manually via `DocumentLinkPicker`.
7. **Tenant deactivate/delete flow** - see Known Limitations.
