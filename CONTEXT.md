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
- `create_admin.py` seed script: `python create_admin.py --email admin@example.com --tenant default` (password from `SDAI_ADMIN_PASSWORD` or prompted; `--tenant` required, the tenant must exist; `--deactivate` blocks an account). Accounts are otherwise managed on the Utilisateurs page (GED Phase 9).
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
- Links are never deleted with a document (GED Phase 6 - documents are no longer hard-deleted): a link to a trashed document is hidden until it is restored, a link to a destroyed document stays and shows the tombstone (record number, type) - see "Lifecycle" below. Only `DELETE /api/db/links/{id}` removes a link.
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

### Classification - Plan de Classement, Tags, Automatic Filing (GED Phase 3)

Code in `apps/api/classification_router.py` (routes under `/api/db`), UI in `SeriesTree`,
`SeriesSection`, `TagsSection`, `LinksSection` and the admin panels on `AccessAdminPage`.

- **Series tree**: `sdai_series` gains `parent_id`, `code` (archival code, e.g. `1.2.3`), `path`
  (materialized `/<root id>/.../<own id>/`, filled by the `sdai_series_default_path` trigger when
  missing), `position` and `system_key`. Names are unique among siblings. `GET /series` (flat, with
  `document_count` of documents the caller can see), `GET /series/tree`, `POST /series` (optional
  `parent_id`), `PATCH /series/{id}` (rename, code, position, move - refuses cycles, rewrites the
  subtree's paths in the same transaction, one move at a time per tenant), `DELETE /series/{id}` only when
  empty (no sub-series, no document incl. trashed, no filing rule). Assigning a document stays
  `PATCH /documents/{t}/{id}/series`.
- **Inherited grants**: a series-scope grant covers every document filed anywhere below the series
  (`documents_router._series_grant_match`: direct id match OR the id in the document series' path).
- **Filters**: the document list/search take `include_subseries=true` (with `series`) and `tag`.
- **Tags**: `sdai_tags` (tenant vocabulary, case-insensitive unique names, optional colour) and
  `sdai_document_tags`. Any user with `edit` on a document tags/untags it (a new name creates the tag);
  `GET /tags` counts only visible documents; rename/recolour/merge/delete are admin-only.
- **Automatic filing**: `sdai_type_series_defaults` (one series per document type).
  `_store_extraction` files a new document in its type's series, else in the tenant's system series
  "À classer" (`system_key='to_be_filed'`, name from `TO_BE_FILED_SERIES_NAME`, shown translated in the
  UI). Admin: `GET /filing-rules`, `PUT /filing-rules/{table}`.
- **Link suggestions**: `GET /documents/{t}/{id}/link-suggestions` - documents whose reference-like
  value (field named like ref/reference/numero/num/number/no, or `record_id`; ≥ 4 characters with a
  digit) appears in this document's text ("mentions"), and documents whose text contains this one's
  references ("mentioned_by", French full-text prefilter + exact match). Excludes itself, already linked
  documents and anything the caller can't see; the UI links one in a click (free-text relation,
  pre-filled "concerne").
- **Sync**: the four tables are synced (series seeded parents first). Paths are derived data: after
  applying a peer's series changes, `repair_series_paths` recomputes them from `parent_id` and breaks a
  cycle two disconnected moves may have created (cut at the smallest id - same result on both nodes).
- **Audit**: `series_updated`, `series_moved`, `series_deleted`, `tag_created`, `tag_updated`,
  `tags_merged`, `tag_deleted`, `document_tagged`, `document_untagged`, `filing_rule_set`.
- **Tests**: `test_classification.py` (paths, cycles, tree), `test_classification_pg.py` (tree, moves,
  cycles, delete-if-empty, inherited grants + leak/count checks, subtree and tag filters, tags, filing,
  suggestions), two-node `test_concurrent_series_moves_converge_without_a_cycle`, permission matrix.

### Full-Text Search in Page Content (GED Phase 4)

Code in `apps/api/page_text.py`, `ingest_router` (page-text worker), `documents_router` (search) and
`apps/api/scripts/backfill_page_text.py`; UI in `GlobalSearch`, `SearchSnippet`, `DocumentsPage`.

- **Search configuration `sdai_fr`**: French stemming + `unaccent` (extension created at startup), so
  "decret" finds "décret". Used by every search query and by the per-type FTS indexes (switched from
  `french`; `_backfill_fts_indexes` rebuilds them at startup).
- **Page text**: `batch_document_pages.ocr_text` (Tesseract transcription of the preprocessed page;
  NULL = not transcribed yet, '' = nothing readable), `manual_text` (values typed for a page entered by
  hand; cleared when the page is redone) and `ocr_tsv` (generated over both, GIN index).
- **Transcription**: every new page row (`_create_page_rows`) is queued for `_page_text_worker`, which
  runs Tesseract in a thread one page at a time - its own lane, never delaying VLM extraction. Local node
  only. `OCR_LANGS` (default `fra+eng`, packs must be installed), `OCR_TIMEOUT_SECONDS` (120),
  `OCR_ENABLED` (true). A failure leaves the page NULL.
- **Backfill**: `python scripts/backfill_page_text.py [--tenant slug] [--sleep 0.2] [--limit N] [--dry-run]`
  on the local node (refuses on the cloud) - pages ingested before Phase 4, pages whose queue entry a
  restart dropped, pages that failed. Idempotent, resumable (each page saved at once), throttled; exit 1
  when some pages could not be read (listed).
- **Search** (`GET /api/db/documents?q=`): `websearch_to_tsquery` (quoted phrase, `-word`, `or`). A document
  matches on its extracted TEXT fields OR on the text of its **current** pages - the pages its
  `page_image_paths` lists (after a re-extraction, the new version's), or its upload's pages for rows
  without that list. A tenant-wide `page_hits` CTE (GIN) prefilters; the exact page check and the page
  rank run per candidate row, inside each table's access-filtered SELECT (live filter included). Ranked:
  2 × field rank + best page rank, then date. Snippets only for the returned page of results:
  `search = {snippet, pages: [{page, snippet}]}`, segments `{text, hit}` (never HTML; 50 page numbers,
  3 snippets per document). JIT is switched off for the search transaction (it cost ~1 s per query).
- **Sync**: generated columns are never sent (`sdai_generated_cols` in the capture trigger and changelog
  seed; ignored on apply) - each node computes `ocr_tsv` from the synced text.
- **UI**: search box at the top of the nav on every page (`/documents?q=`), the Documents page keeps the
  search in the URL, results show highlighted snippets and "p. N" badges that open the viewer at that
  page (`/documents/t/id?page=N`); a match found only in page text opens at its first matching page.
- **Benchmark** (`test_search_benchmark_pg.py`, `-m benchmark` + `TEST_DATABASE_URL`): 2 000 documents /
  10 000 pages - rare word, common word, phrase, exclusion, all-documents field match: 60-110 ms median
  per request on a laptop.
- **Tests**: `test_search.py` (helpers, SQL shape), `test_search_pg.py` (config, real Tesseract read,
  manual text, sync without the vector, backfill, accents, phrases, page numbers, ranking, permission
  leak + counts, trashed, current version only).

### Download, Share Links, Selective Export (GED Phase 5)

Code in `apps/api/document_files.py` (file resolution, names, watermark), `versions_router` (download),
`share_router.py` (share links, public `/s/` routes) and `export_router.py` (export jobs); UI in
`DownloadOriginalMenu`, `ShareDialog`, `DocumentsPage` (multi-select), `ExportsPage`.

- **Original download** `GET /api/db/documents/{t}/{id}/download[?version=N&inline=true]` (permission
  `download`): the untouched file of the current version (version 1 = the ingested original) or of version
  N, named `<record_id>_<original name>` (ASCII fallback + RFC 5987 `filename*`). Every call is audited
  (`document_downloaded`, `via: download`, file hash). File not copied to this node yet by sync → **409** +
  `X-Sync-Pending: 1` (the older `/versions/{n}/file` keeps its 404 for the viewer). With
  `DOWNLOAD_WATERMARK_CONFIDENTIAL=true`, PDFs of confidential/secret documents are stamped in memory
  (level + who + when, pypdf) - the stored file never changes; a PDF that can't be stamped is refused (422),
  never sent unmarked.
- **Share links** `POST/GET /api/db/documents/{t}/{id}/shares`, `DELETE .../shares/{id}`: needs
  `can_share` (admin always) AND `download` on the document; `secret` never shareable, `confidential` by
  admins only. Expiry 1-30 days (default 7), optional bcrypt password (≥ 4 chars), optional max downloads
  (≤ 1000), pinned version or always-latest, recipient note. Token = 32 random bytes, only its sha256 is
  stored (`sdai_share_links.token_hash`); the URL (`PUBLIC_BASE_URL` + `/s/<token>`, else the request's
  host) is returned once. Admins and access managers see/revoke every link of a document; others their own.
- **Public routes** (the only unauthenticated data routes - CLAUDE.md): `GET /s/{token}` renders a
  self-contained page (no app bundle, no script; fr/en from `Accept-Language`; `no-store`, `noindex`):
  file name, expiry, size, downloads left, password field. `POST /s/{token}/download` checks the password,
  locks the link row, counts and streams the file. Every call re-checks: link not revoked/expired/used up,
  document live, and its **creator** still active, still `can_share`, still holding `download` and the
  clearance (`auth.load_user` + `_document_visible`) - a link dies with its creator's access. Every failure
  (malformed, unknown, expired, revoked, used up, access lost) is the same 404 page. Rate limits per IP:
  30/min GET, 10/min POST. Audit `share_link_accessed` (IP, event `viewed` (5-min dedupe) / `downloaded` /
  `password_failed`, no user), `document_shared`, `share_link_revoked`; access events are hidden from the
  document history for non-managers. A POST-only download means link previewers never use up a download.
- **Download counting**: each download through a link is one row of `sdai_share_downloads` (insert-only,
  synced without a row clock, so the two nodes' counts never conflict); the count is `count(*)`.
- **Selective export** `POST /api/db/export` {`documents[]` | `series_id` + `include_sub` | `search`
  {q, type, series, tag, reviewer, dates...}}, `versions` current|all → 202 job. The selection is resolved
  at creation through the list's access clause at the **download** level (undownloadable ids silently
  dropped; nothing left = 422; > `EXPORT_MAX_DOCUMENTS` or > `EXPORT_MAX_MB` = 413) and frozen in the job. A
  background task (`EXPORT_CONCURRENCY` at once) writes `DATA_DIR/generated/exports/<tenant>/<job>.zip`:
  `files/` (originals), `metadata.json` (identity, series path, tags, extracted fields, each file's recorded
  and actual sha256 + status `ok`/`unverified`/`hash_mismatch`/`missing` - no server paths), `metadata.csv`
  (UTF-8 BOM), `manifest.sha256` (`sha256sum -c` format, every other entry) and `LISEZMOI.txt`/`README.txt`
  (how to verify, warnings). `GET /api/db/export/jobs[/{id}[/download]]`, `DELETE .../{id}` - owner only,
  ZIP kept `EXPORT_LINK_HOURS` (24). Audit `documents_exported` (ids + ZIP hash, when built) and
  `export_downloaded`. `sdai_export_jobs` is node-local (never synced); interrupted jobs are failed and
  expired ZIPs deleted at startup and on every list/create. The admin full-tenant export is unchanged.
- **UI**: header of the document page - "Download original" (current + version menu, errors shown) and
  "Share" (dialog: options, link shown once with copy, list with status/downloads, revoke), shown only when
  allowed; Documents page - checkbox per card, "Export selection" / "Export all results" (current
  filters) with current/all versions; "My exports" page (`/exports`, polls while building, download,
  delete, warnings).
- **Tests**: `test_share_export.py` (helpers, ZIP builder, escaping, only the known routes are
  unauthenticated), `test_share_export_pg.py` (35: download, versions, 403/404/409, watermark; share rules,
  token never in clear (DB, audit, changelog), landing/download, expired, revoked, password, max downloads,
  creator losing access/deactivated, document turned secret, pinned version; export by ids/series/search,
  leak checks, all versions, limits, missing/tampered files flagged, manifest verified, privacy, expiry,
  recovery, never synced, admin export unchanged); permission matrix.

### Lifecycle, Trash, Retention, Legal Hold, Destruction (GED Phase 6)

Code in `apps/api/lifecycle.py` (state machine - the ONLY writer of `lifecycle_status`, the trash
columns and `archived_at`), `retention.py` (rules, dates, daily job), `destruction.py` (proposals,
execution, file deletion on both nodes, PV), `pdf_report.py` (small PDF writer) and `lifecycle_router.py`
(routes); UI in `LifecycleBadge`, `LifecycleSection`, `TrashPage`, `RetentionRulesPage`, `DestructionsPage`.
"Lifecycle manager" (archivist) = admin, or reviewer with `can_manage_lifecycle`.

- **States**: `active → archived` (archive, lifecycle manager with edit), `archived → destruction_eligible`
  (retention job only), `destruction_eligible → archived` (extend: a manual date `retention_override` that
  wins over the rules), `destruction_eligible → destroyed` (approved destruction only); a trashed document
  past `TRASH_RETENTION_DAYS` goes to `destroyed` through the purge. Anything else is a 409
  (`lifecycle.check_transition`). Archived / eligible documents are **read-only** except for lifecycle
  managers: `lifecycle.editable_sql` is folded into `_access_where_clause(..., "edit")`, so every edit-level
  action (fields, review, versions, tags, series, links, page actions, the review queue) refuses them at
  once (403 `lifecycle.read_only`; `my_permission` drops to download). New `_GED_COLS`: `deleted_reason`,
  `archived_at/by`, `retention_rule_id`, `retention_override`, `destroyed_at`, `destruction_id`.
- **Trash** (replaces every hard delete): `POST .../trash {reason}` (also `DELETE /api/review/{t}/{id}`, now
  lifecycle manager + edit), `POST .../restore` (within the retention, refused 409 when the same file was
  uploaded again meanwhile), `GET /api/db/trash`. Links, grants, tags and versions stay, hidden with the
  document (links to a trashed document are left out of `GET .../links`). Ingest: removing an upload trashes
  the document it filed (`_trash_document_by_batch_document_id`, reason code `upload_removed`; an upload
  that filed nothing is still deleted); removing a batch trashes its documents (`batch_removed`), deletes the
  upload rows that filed nothing, and deletes the batch only when empty - else `batches.removed_at` hides it
  from `GET /api/ingest/batches`. Refused as a whole when a document is held. `_retry_document` still
  deletes the crashed stub it replaces (internal, no content). Audit `document_trashed` / `document_restored`.
- **Dedup decision**: only LIVE documents are duplicates. The per-type `content_hash` unique index is partial
  (`WHERE deleted_at IS NULL AND lifecycle_status IS DISTINCT FROM 'destroyed'`,
  `_ensure_live_content_hash_index` replaces the old plain index once at startup) and `_find_duplicate`
  filters the same way - the trashed row stays as the trace of the removed upload (its hashes kept), the
  same file can be uploaded again, and restoring the trashed one is then refused (`lifecycle.live_duplicate`).
- **Retention** (`sdai_retention_rules`): scope `series` (covers sub-series) or `doc_type`, `duration_months`,
  `trigger_on` `document_date` (extracted field `date_field`: ISO, d/m/Y, "8 mars 2021") | `ingested_at` |
  `reviewed_at` | `archived_at`, `final_action` `destroy` | `review` | `keep_forever`. Most specific wins
  (`pick_rule`: nearest series up the path, then the type). Stored on the document (`retention_until`,
  `retention_rule_id`) by `retention.recompute` - on every rule change and series move (whole tenant), on
  series assignment, field edits, review, re-extraction and new documents (one document), and by the daily
  job. Routes `GET/POST/PATCH/DELETE /api/db/retention-rules` (lifecycle managers), `POST /api/db/retention/run`
  (admin, local node), `POST .../retention/extend {until}`. Audit `retention_rule_*`, `retention_extended`.
- **Daily job** (`retention.daily_worker`, local node only, session advisory lock,
  `RETENTION_JOB_INTERVAL_HOURS`): recompute, then archived + untrashed + unheld documents whose
  `retention_until` has passed (and rule not keep_forever) → `destruction_eligible` (audit
  `document_destruction_eligible`, `sdai_notifications` row `retention_eligible` for each lifecycle manager -
  the notification UI/e-mail is Phase 7), then the trash purge. Both `destroy` and `review` outcomes make a
  document eligible - destruction always needs a proposal + approval; `review` tells the archivist to
  re-examine (extend or propose).
- **Legal holds** (`sdai_legal_holds` + `sdai_legal_hold_documents`): on a document, a series (recursive, also
  for documents filed later) or a search result (frozen list via `_run_list_query`, ≤ 5000);
  `POST /api/db/legal-holds`, `POST .../{id}/release {reason}`, `GET /api/db/legal-holds`. The active holds
  are the only authority (`lifecycle.held_sql`), checked in SQL at the moment of trash, purge, proposal,
  approval and the move to eligible; the document's `legal_hold` column is a display copy
  (`refresh_hold_flags` after holds change, series moves and assignments). Audit `legal_hold_set/released`.
- **Destruction** (`sdai_destruction_records`): a lifecycle manager proposes eligible, unheld documents not in
  another pending proposal (`POST /api/db/destructions`) - the record freezes record_id, type, series, dates
  and every file hash; admins are notified. An admin **other than the proposer** approves (also a DB CHECK)
  → in one transaction every document becomes a tombstone (`lifecycle.tombstone`: extracted fields and file
  references nulled, record_id / type / series / dates / hashes kept, status `destroyed`), its versions,
  field history, tags and document grants are deleted, share links revoked, page rows deleted, upload paths
  nulled; every file it used goes into `sdai_destroyed_files` (except files another live document still
  uses). One document that can't be destroyed (hold placed meanwhile...) → 409, nothing destroyed. After the
  commit the files are deleted on this node and the PV is written. Reject (admin) / cancel (proposer) change
  nothing. Links stay and show the tombstone (`other_destroyed`, `other_record_id`). Audit
  `destruction_proposed/approved/rejected/cancelled`, `document_destroyed`.
- **Trash purge**: `destruction.purge_trash` (daily job) - trashed > `TRASH_RETENTION_DAYS`, unheld, up to
  `TRASH_PURGE_BATCH` per run, one record `kind='trash_purge'`, `status='approved'` without people, comment
  "purge corbeille", same execution path.
- **PV** (procès-verbal d'élimination): `pdf_report.py` writes the PDF directly (A4, Helvetica WinAnsi,
  wrapped tables, page numbers - no layout library in the image) into
  `DATA_DIR/generated/destruction/<tenant>/ELIM-<year>-<id8>.pdf`, French, plus an English part when
  `sdai_tenants.locale` starts with `en` (`create_tenant.py --locale en`); identity, people, dates, reason,
  documents with fingerprints grouped per file, files deleted, the restic warning, signature lines.
  `GET /api/db/destructions/{id}/pv` (regenerated if missing on this node). The record's `file_path` syncs
  the PV to the peer like any file.
- **Both nodes** (sync 'destroy' event): `sdai_destroyed_files` is synced insert-only; `purge_destroyed_files`
  deletes the listed files still on disk (only inside the sync file roots), drops them from the file queue
  and records them in node-local `sdai_destroyed_files_done` - run after the destruction, after applying a
  peer's changes (`sync.store.after_apply`) and every `DESTROYED_FILES_SWEEP_SECONDS` on every node
  (`destroyed_files_worker`, which also runs `heal_tombstones`: version/page/history/tag/grant rows a node
  restored from an old backup sends back for a destroyed document are deleted). A destroyed path is never
  queued (`files.enqueue`), transferred (worker) or received (`PUT /api/sync/files` → 403) again.
- **UI**: lifecycle badges (archived, à éliminer, éliminé, gel juridique) in the document header and cards;
  "Cycle de vie" panel on the record tab (status, retention date + rule, holds, actions the backend allows -
  archive, trash with reason, keep longer, place/release hold); sidebar group "Cycle de vie" for lifecycle
  managers with a badge (eligible not yet proposed + proposals awaiting another admin): **Éliminations**
  (`/lifecycle/destructions`: select + propose, records with approve/reject/withdraw, documents and hashes,
  PV download), **Règles de conservation** (`/lifecycle/retention`: rules, run now, legal holds panel),
  **Corbeille** (`/lifecycle/trash`); Documents page "Legal hold on the N results". fr/en. Clicked through
  in Chrome against a test database (archive, hold, propose, approve, PV download).
- **Tests**: `test_lifecycle.py` (route guards for every lifecycle route, transition table, read-only rule,
  edit clause, hold SQL, dates, rule precedence, PV content fr/en, report pagination),
  `test_lifecycle_pg.py` (20 against PostgreSQL: transitions, read-only, trash/restore/links, old delete
  routes, dedup after trash, trash expiry, precedence + recompute, document date, daily job + notifications +
  extend, holds (series recursive, document, search), two-person destruction + tombstone + files + PV fr/en,
  hold after proposal, shared file kept, trash purge, destroyed files not re-queued, tombstone healing),
  two-node `test_destruction_deletes_files_on_both_nodes`, vitest `lifecycle.test.ts` (payloads captured
  from the API). Upgrade from the Phase 5 schema checked (index replaced, columns/tables added, idempotent).

### Notifications, Check-out, Comments, Electronic Signature (GED Phase 7)

Code in `apps/api/notifications.py` (notify helper, recipients, e-mail outbox + sender, digest, ops
alerts), `notifications_router.py`, `locks.py` + `checkout_router.py`, `comments_router.py`,
`signatures.py` (pyHanko PAdES signing/verification, image → PDF) + `signature_router.py`,
`scripts/generate_signing_cert.py`; UI in `NotificationBell`, `NotificationsPage`, `CheckoutControl`,
`CommentsSection`, `SignaturesSection` (document tabs "Commentaires" / "Signatures", `?tab=`).

- **Notifications** (`sdai_notifications`, synced; + `table_name`, `document_id`): `notifications.notify(conn,
  tenant, users, kind, payload, table_name=, document_id=, coalesce=, exclude=)` runs in the caller's
  transaction under a savepoint and never raises. Kinds: `review_pending` (after `_store_extraction` when not
  auto-approved, to admins/reviewers who can edit it, coalesced into one unread "N to review"),
  `document_shared` (document grant → the person / group members who can read it), `mention`,
  `comment_reply`, `retention_eligible`, `destruction_proposed`, `sync_down` / `backup_failed` (each node
  checks its own state every `OPS_ALERT_CHECK_SECONDS`, notifies all admins once per occurrence -
  `sdai_ops_alerts` node-local), `signature_requested` / `_completed` / `_declined`, `lock_released`.
  Recipients are always re-checked with `_document_visible`; the list endpoint re-checks access too and
  strips the document reference + payload of notifications about documents no longer readable.
  Routes: `GET /api/db/notifications`, `/unread-count`, `POST .../{id}/read`, `/read-all`,
  `GET|PUT /api/db/notification-preferences` (`sdai_users.email_notifications` immediate/digest/off,
  `sdai_users.locale` - set at first login when empty).
- **E-mail** (`sdai_email_outbox`, **node-local** - the node that queued an e-mail sends it): queued only
  when `SMTP_HOST` + `SMTP_FROM` are set, one row per `dedupe_key` (`n:<notification>`,
  `digest:<user>:<date>`), subject/body from fixed fr/en sentences (`email.*` in i18n.py) + a link on
  `PUBLIC_BASE_URL`, never document content. `email_worker` (every node) claims due rows with SKIP LOCKED,
  sends over SMTP (`SMTP_SECURITY` starttls/ssl/none), retries with backoff (1 min … 6 h) up to
  `EMAIL_MAX_ATTEMPTS`, reclaims rows stuck in 'sending' after 10 min (at-least-once; Message-ID = outbox
  id). `digest_worker` (local node) after `EMAIL_DIGEST_HOUR`: unread notifications of the last 24 h.
  Admins see this node's failed / retrying e-mails on the Synchronisation page ("E-mails envoyés par ce
  serveur": `GET /api/admin/emails`, `POST /api/admin/emails/retry|discard {ids?}`, tenant-scoped, audited
  `emails_retried` / `emails_discarded`) and a sync-status alert `emails_failed`.
- **Check-out** (`locked_by` / `locked_until` on every per-type table): `locks.unlocked_sql` is folded into
  the `edit` level of `_access_where_clause` **for admins too**, so every write path refuses a document
  checked out by someone else; `_require_document_permission` answers **423** "Verrouillé par X" (page
  actions answer 404 through their guard). Locked documents leave the others' review queue. Routes
  `GET .../lock`, `POST .../checkout {hours}` (edit; default `CHECKOUT_DEFAULT_HOURS`=8, max
  `CHECKOUT_MAX_HOURS`=72; the holder re-checks out to extend), `POST .../checkin` (multipart, holder;
  optional file → next version via `versions_router.add_uploaded_version`, comment, reextract),
  `POST .../unlock` (admin, notifies the holder). Expired locks are free at once; `locks.expiry_worker`
  (local node, `LOCK_SWEEP_SECONDS`) clears them. Archived documents can only be checked out by lifecycle
  managers (edit level). Across nodes the lock is a synced column - advisory while they are disconnected.
  Audit `document_checked_out`, `document_checked_in` (`details.reason`: checkin / forced / expired).
- **Comments** (`sdai_comments`, synced, no FK on `parent_id` - the peer applies deletes row by row):
  read access to read; reviewer role + read access to post (also on checked-out / archived documents);
  author edits; author or admin deletes (soft). `mentions` = user ids, filtered to people who can read the
  document (`/comments/mentionable?q=`); replies notify the parent's author. Audit `comment_added` /
  `_edited` / `_deleted` (ids, lengths, mentions - never the text).
- **Electronic signature** - simple / advanced, PAdES-B-B, never "qualified":
  `sdai_signature_requests` (ordered `signers` JSONB, status pending/completed/declined/cancelled, one
  pending request per document) and `sdai_signatures` (evidence: version signed and produced, field name,
  signer, title, reason, time, IP, certificate subject + SHA-256 fingerprint, hashes before/after), both
  synced. Requesting needs edit; signers must be able to read. Signing (`POST
  /api/db/signature-requests/{id}/sign {password, reason?}`, next signer only): bcrypt re-check of the
  password (failures audited `signature_password_failed`), then under the version row lock
  (`_lock_for_change`: check-out 409, archived 403) the current version is signed with the organisation
  certificate (`SIGNING_P12_PATH` / `SIGNING_P12_PASSWORD`, cached by path + mtime) as an incremental update
  with a visible stamp (bottom-left of the last page, stacked) → a new version `v<N>_<name>_signe.pdf`; an
  image version is first converted (img2pdf, PDF/A-2b via `ocrmypdf --skip-text` when installed - in the API
  image) as a version of its own. 503 when no certificate. `GET .../signatures?version=` verifies every
  embedded signature (intact, trusted against the org certificate + `SIGNING_TRUST_ROOTS`, coverage) and
  matches it with the evidence + the audit events. Decline / cancel close the request. Audit
  `signature_requested`, `document_signed`, `signature_declined`, `signature_request_cancelled`.
- **Destruction** also deletes the document's comments, notifications, signature requests and evidence
  (execute + `heal_tombstones`).
- **Tests**: `test_collab.py` (mocked helpers: e-mail content, backoff, links, lock SQL, file types),
  `test_collab_pg.py` (37 against PostgreSQL: notify/coalesce/savepoint, outbox idempotency + sender
  retries/give-up/stale claims, digest, notification API + leak, preferences, share/review notifications,
  ops alerts; lock on every write path for admins and archivists, check-in with version, extend, rights,
  forced unlock, expiry, review queue; comments rights/leaks/mentions/replies/edit/delete/destruction;
  signing status, request rules, ordered signing with a generated certificate → new versions validating with
  pyHanko, image conversion, tampering detected, lock/lifecycle rules, reader signer, decline, cancel,
  destruction), two-node `test_collaboration_rows_sync_but_the_outbox_stays`, vitest
  `collaboration.test.ts`. Upgrade from the Phase 6 schema checked (tables, columns, sync triggers, outbox
  untouched, idempotent). UI clicked through in headless Chromium (request, comment with mention, check-out,
  lock seen by another user, notifications, check-in, sign, verification).

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

### Hardening, User Management, Offline Install, Manuals (GED Phase 9)

- **User management** (`users_router.py`, `UsersPage` at `/ops/users`, `ChangePasswordPage` at
  `/account/password`): admins and holders of `can_manage_users` create / update / deactivate /
  reactivate / reset; delete = admins only, soft (`deleted_at`, the live e-mail replaced by the tombstone
  `<id>@deleted.invalid`, the original in `deleted_email`). Guards: no escalation by non-admin managers
  (`_check_grants`, `_check_manageable`), never on oneself or the last active admin. Temporary password →
  `must_change_password` → 403 + `X-Password-Change-Required` everywhere except `PASSWORD_CHANGE_PATHS`;
  a token issued before `password_changed_at` is refused (401). References to deleted users: SQL
  `audit.display_email_sql()`, frontend `replaceDeletedUsers` in api-client ("Utilisateur supprimé du
  système"); signed PDFs and destruction PVs keep the name. Group membership stays admin-only (Accès &
  Groupes).
- **Security review (9.1)**: every route walks to `get_current_user` (except login/logout, `/s/{token}`
  and HMAC sync); findings fixed are listed in CHANGELOG 2.0.0. `tests/test_hardening.py` also checks that
  the API Dockerfile copies every imported module and that no compose build context is git-ignored.
- **i18n (9.2)**: fr/en catalogues key-identical (`tests/test_i18n_completeness.py`), backend messages
  en+fr; `DEFAULT_LANGUAGE` → frontend meta `sdai-default-language` (rewritten by the entrypoint at start)
  and API `DEFAULT_LOCALE`; `utils/dates.ts` / `i18n.format_date` (fr dd/MM/yyyy).
- **Offline install (9.3)**: `setup.sh` (internet) → `models/{docker-images,ollama-models}.tar.gz` +
  `SHA256SUMS`; `install.sh` (offline) → checksum, `docker load`, `scripts/first-run.sh` (`.env` via awk
  helpers, secrets from `/dev/urandom`, `COMPOSE_FILE` per role, `sync.join` once on a cloud node
  (`SDAI_JOINED`), `create_tenant.py --slug default` + `create_admin.py`, `sync.check`). Verified from an
  Ubuntu 24.04 client for a local and a cloud node. Frontend entrypoint: per-installation self-signed
  certificate (volume `frontend_ssl`, IP SAN for an IPv4 CN), http → https redirect keeps `HTTPS_PORT`.
  Container logs capped (`x-logging` in compose).
- **Manuals (9.4)**: `docs/manuals/guide-utilisateur.md`, `guide-administrateur.md`,
  `fiche-exploitation.md` (French; every quoted label is a fr.json string).
- **Performance (9.5)**: `_access_where_clause` builds its grant checks with `_grant_targets_sql` - one
  EXISTS per scope; the series scope looks up the document's series once and probes
  `sdai_document_access (tenant_id, series_id)` with `= ANY(ancestor ids from path + own id)`. The old
  single OR made PostgreSQL test every series grant against every document (2.5 s for 20 000 documents
  with 50 series grants; now ~25 ms). JIT is off on every app connection
  (`ingest_router._set_node_id_on_connect`) and per list query. Benchmarks:
  `tests/test_search_benchmark_pg.py -m benchmark` (`BENCH_DOCS=20000` = 100 000 pages, production-like
  grants); results in docs/ged/PROGRESS.md (local, git-ignored) and CHANGELOG 2.0.0.

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

- `setup.sh` (internet-connected machine): pulls / builds the images for `TARGET_PLATFORM` (default
  linux/amd64), saves them to `models/docker-images.tar.gz`, bundles the Ollama model store as
  `models/ollama-models.tar.gz` (skipped if present), writes `models/SHA256SUMS`; `--dev` also wheels and
  a pnpm store.
- `install.sh` (offline server): verifies `SHA256SUMS`, `docker load`, then `scripts/first-run.sh` (see
  GED Phase 9). Re-running it with a newer bundle updates the node and keeps `.env` and data.
- `docker-compose.yml` Ollama service: restores `ollama-models.tar.gz` when its volume has no model,
  falls back to `ollama pull`; the `api` service starts after Ollama is healthy.

### Request Timing Middleware

- `api_server.py` includes an HTTP middleware that measures wall-clock time per request using `time.perf_counter()`.
- Every response carries an `X-Process-Time` header (4 decimal places, e.g. `0.0123`).
- Requests exceeding **2.0 s** emit a `WARNING` log: `Slow request: METHOD /path took X.XXXXs`.

### Nginx and Images

- No nginx cache on `/images/` any more (GED Phase 9.1): a cached response was served to anyone
  without the API's access check. The API answers image requests with `Cache-Control: private`, so
  only the user's own browser caches them.

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
| VLM model selector | Not built - only `vlm_qwen25_results.json` is wired to the VLM tab |
| Mobile / responsive layout | Not tested - designed for desktop |
| Bulk review actions | Not built - documents are approved/rejected one at a time |
| Audit log user filter in UI | Backend supports `user_id` filter but UI exposes only `action` and date range |
| Tenant deactivate/delete flow | `sdai_tenants.is_active` exists but nothing sets it to `false` - retiring a tenant currently means a manual SQL `UPDATE` |
| Index names on long table names | `idx_<table>_<col>` can exceed Postgres's 63-char limit and get truncated - on a very long `t_<tenant>_<type>` name two GED indexes may collide and one is silently skipped (same pre-existing pattern as `series_id`/`uploaded_by` indexes) |
| Admin full export includes trash and tombstones | The admin full-tenant export (`/api/admin/export`) is a complete copy: trashed documents and destroyed tombstones are included with their lifecycle columns (deliberate - it is the backup-style export); selective exports only take live documents |
| Batch list / status visible tenant-wide | `GET /api/ingest/batches` and `/status/{batch_id}` still show every contributor of the tenant all batches (counts, filenames, document types) - the pipeline is shared by design; page contents, images and actions do follow the filed document's access |
| API keys can no longer steer page extraction | Since GED Phase 1 an API key reaches only upload, presets, batches, status and page listing - retry/reload/skip/cancel/pause/resume need a reviewer |
| Version files outside the viewer | A new version without re-extraction is stored and downloadable from the Versions tab, but the page viewer keeps showing the pages of the last extracted file |
| Re-extraction keeps the document type | A version whose extraction detects another document type stays in its table; the detected type is only noted in the `document_reextracted` audit entry |
| Destroyed files in backups | Restic snapshots taken before a destruction keep the files until they expire (≈ 1 year); stated in the PV and RESTORE.md - forgetting snapshots is a manual restic operation |
| Audit details after destruction | The append-only audit log keeps what it recorded before (e.g. `document_fields_edited` changes with old/new values); a destruction cannot erase it (it would break the hash chain) |
| Retention on active documents | Retention only ends for archived documents: a document never archived is never proposed for destruction, whatever its rule |
| Document-date rules | `trigger_on=document_date` needs a parseable extracted date (ISO, d/m/Y, "8 mars 2021"); documents without one get no retention date (never eligible) - shown as "No retention" |
| E-mail delivery | At-least-once: a crash between the SMTP answer and the status update resends the e-mail (same Message-ID). Each server's outbox is only visible on that server's Synchronisation page |
| Digest timing | Daily digests are built by the local node after `EMAIL_DIGEST_HOUR` (server time) - none while the local node is down |
| Review notifications | One coalesced notification per reviewer ("N documents to review"); with immediate e-mail the reviewer gets one e-mail until they read it |
| Check-out across nodes | While the nodes can't reach each other, the same document can be checked out on both sides; edits then meet the sync conflict rules |
| Signature certificate | One organisation certificate per node (not per tenant or per signer); a self-signed one shows as "validity unknown" in PDF readers outside SDAI. No timestamp authority (PAdES-B-B, no B-T/LTV) - the time is the server's |
| PDF/A conversion | Only with `ocrmypdf` installed (it is in the API image); otherwise image originals are converted to plain PDF before signing |
| Signed PDFs not displayed | The viewer still shows the page images of the last extracted file; the signed PDF is in the Versions tab / download menu |
| Legal holds on search results | A search hold freezes the documents found at that moment; documents matching the same search later are not held (use a series hold for that) |
| Audit chain vs. a DB superuser | The chain shows edits, deletions and truncation, but someone able to rewrite both the log and `sdai_audit_chain_heads` could recompute it undetected - the replicated copy on the other node (GED Phase 8) is the external witness |
| Sync topology | Exactly one peer per node (`SYNC_PEER_URL`), no relaying - a third node would need its own pairing; the local node must be able to reach the cloud (not the reverse) |
| Sync and file deletion | Files are only ever deleted by a destruction (both nodes, `sdai_destroyed_files`); deleting any other row never deletes files |
| Unique keys created on both sides | Two different rows with the same natural key created offline on both nodes (e.g. the same e-mail for two new users) can't both exist: the second arrives as a `unique_violation` conflict to settle by hand |
| Cache invalidation across processes | The sync worker clears the API's caches through Redis; without Redis (in-memory cache) cached counts/schema on the API side refresh only when they expire |
| HLC counter wrap | Two changes of one node in the same millisecond after 100 000 others (counter wrap) could be ordered wrongly - not reachable in practice |
| Hourly-scale clock skew | Sync requests are refused beyond ±5 min skew - both nodes must run NTP |
| Conflicts per node | Each node keeps its own conflict list; resolving on one closes the other's copy only when the resolution reaches it (next cycle) |
| UI not clicked through | The Synchronisation page and badges, the classification UI (series tree, tags, suggestions, filing rules), the search UI (nav search box, snippets, page badges) and the Phase 5 UI (download menu, share dialog, multi-select export, My exports) were type-checked and built, not tested in a browser (the Phase 6 lifecycle UI and the Phase 7 collaboration UI were clicked through) |
| Existing documents not auto-filed | Automatic filing applies to documents filed from Phase 3 on; older documents without a series stay unfiled until assigned |
| Link suggestions are lexical | Only exact reference strings are matched (no fuzzy match on "N° 123" vs "N°123") |
| Series names on sync | Two series with the same name created under the same parent on both nodes while disconnected arrive as a `unique_violation` sync conflict |
| Page text quality | Tesseract on the preprocessed image: handwriting, stamps and poor scans give little or noisy text; PDFs' own text layers are not used |
| Page-text queue in memory | Pages queued for transcription when the API stops are not resumed automatically - run `scripts/backfill_page_text.py` (it also retries failed pages) |
| Old versions not searchable | Only the pages the viewer shows now are searched; text of replaced versions is kept but not matched |
| Search ranking | `ts_rank` only (no recency/popularity boost); a document whose fields AND pages match ranks by field rank × 2 + its best page |
| Share download limit across nodes | Each node enforces `max_downloads` on the downloads it has seen; a link used on both nodes before they sync can exceed it by the downloads made in between |
| Pinned share versions | A link pinned to a version keeps serving that version after newer ones are added (by design) - revoke it to stop it |
| Export ZIPs stay on one node | An export job and its ZIP exist only on the node that built it; the selection is frozen at creation (access changes in the 24 h window don't shrink a finished ZIP) |
| Export disk use | ZIPs are built on disk next to the data (`DATA_DIR/generated/exports`), up to `EXPORT_MAX_MB` each, deleted after `EXPORT_LINK_HOURS` |
| Watermark | Standard Helvetica (WinAnsi): characters outside it print as "?"; only PDFs are stamped (images are sent as stored) |
| Low disk space | No alert in the app - the operations sheet asks for a daily `df -h` check |
| Sync file transfers | One file at a time; on a 2 Mbps / 50 ms link files use ~95 % of the bandwidth, rows ~1 000 per 25 s. After an outage the next attempt waits for the current backoff (up to 15 min) |
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
  classification_router.py          # /api/db series tree, tags, filing rules, link suggestions
  page_text.py                      # page transcription (Tesseract), manual page text, sdai_fr search config
  document_files.py                 # a document's file (version), safe-root/sync checks, download name, PDF watermark
  share_router.py                   # /api/db/.../shares + public /s/{token} page and download
  export_router.py                  # /api/db/export - selective export jobs (ZIP + metadata + sha256 manifest)
  lifecycle.py                      # state machine - the only writer of lifecycle_status / trash / archived_at; legal-hold SQL
  lifecycle_router.py               # /api/db archive, trash, restore, retention rules, legal holds, destructions
  retention.py                      # retention rules (precedence, dates, recompute) + daily job (local node)
  destruction.py                    # proposals, two-person approval, tombstones, file deletion on both nodes, PV
  pdf_report.py                     # small text PDF writer (destruction PV)
  notifications.py                  # notify() + recipients, e-mail outbox + sender, daily digest, ops alerts (GED Phase 7)
  notifications_router.py           # /api/db/notifications, unread count, read, preferences
  locks.py                          # check-out lock SQL (folded into the edit level), checkout/release, expiry sweep
  checkout_router.py                # /api/db/.../lock, checkout, checkin (+ new version), unlock (admin)
  comments_router.py                # /api/db/.../comments (threads, @mentions) + /api/db/comments/{id}
  signatures.py                     # PAdES signing/verification (pyHanko), organisation certificate, image → PDF/A
  signature_router.py               # signature requests, sign (password), decline, cancel, verification + evidence
  scripts/generate_signing_cert.py  # self-signed organisation certificate (.p12) for tests / first install
  scripts/backfill_page_text.py     # transcribe pages without text (local node; resumable, throttled)
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
    test_search.py                  # GED Phase 4 search helpers (mocked)
    test_search_pg.py               # GED Phase 4 page text, backfill, search on a real PostgreSQL (opt-in, -m pg)
    test_search_benchmark_pg.py     # GED Phase 4 10 000-page search benchmark (opt-in, -m benchmark)
    test_share_export.py            # GED Phase 5 download/share/export helpers + unauthenticated-route guard (mocked)
    test_share_export_pg.py         # GED Phase 5 download, share links, exports on a real PostgreSQL (opt-in, -m pg)
    test_lifecycle.py               # GED Phase 6 route guards, transitions, retention helpers, PV (mocked)
    test_lifecycle_pg.py            # GED Phase 6 trash, retention, holds, destruction on a real PostgreSQL (opt-in, -m pg)
    test_collab.py                  # GED Phase 7 helpers: e-mail content, backoff, lock SQL, signature file types (mocked)
    test_collab_pg.py               # GED Phase 7 notifications, outbox, check-out, comments, signatures (opt-in, -m pg)

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
  pages/DocumentDetailPage.tsx      # single document - tabs Record / Versions / History / Comments / Signatures (?tab=)
  pages/NotificationsPage.tsx       # notifications list, documents to sign, e-mail preferences
  components/NotificationBell.tsx   # sidebar bell with unread count (polls every minute)
  components/CheckoutControl.tsx    # header lock badge, Extraire / Réintégrer / forced unlock
  components/CommentsSection.tsx    # "Comments" tab: threads, @mention autocomplete, edit/delete
  components/SignaturesSection.tsx  # "Signatures" tab: requests, sign / decline / cancel, verification + evidence
  hooks/useCollaboration.ts         # notifications, lock, comments, signatures queries + mutations
  utils/notifications.ts            # notification text per kind + tab-aware links
  pages/SchemaPage.tsx              # schema inspector
  pages/ReviewPage.tsx              # review queue
  pages/AuditPage.tsx               # audit log (admin only)
  pages/ExportsPage.tsx             # "My exports": selective export jobs, ZIP download
  pages/TrashPage.tsx               # "Corbeille": trashed documents, restore (lifecycle managers)
  pages/RetentionRulesPage.tsx      # "Règles de conservation" + legal holds panel
  pages/DestructionsPage.tsx        # "Éliminations": eligible documents, proposals, approval, PV download
  components/LifecycleBadge.tsx     # archived / to be destroyed / destroyed / legal hold badges
  components/LifecycleSection.tsx   # document "Lifecycle" panel: status, retention, holds, actions
  components/auth/LifecycleRoute.tsx # keeps non-archivists off the lifecycle pages
  components/DocumentList.tsx
  components/OcrPanel.tsx
  components/VlmPanel.tsx
  components/LinksSection.tsx       # "Related documents" panel on DocumentDetailPage
  components/DocumentLinkPicker.tsx # searchable picker for creating a new link
  components/VersionsSection.tsx    # "Versions" tab: list, open/download, restore, upload (+ re-extract)
  components/HistorySection.tsx     # "History" tab: field before/after timeline + document events
  components/GlobalSearch.tsx       # nav search box (every page) → /documents?q=
  components/SearchSnippet.tsx      # highlighted search snippet (segments, rendered as text)
  components/DownloadOriginalMenu.tsx # "Download original" + version menu (document header)
  components/ShareDialog.tsx        # "Share": create a link (shown once), list, revoke
  hooks/useSharing.ts               # share links, export jobs, file download mutations
  hooks/useLifecycle.ts             # lifecycle, trash, retention rules, legal holds, destructions
  utils/lifecycle.ts                # date formatting for lifecycle views
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
  documents_db.ts                   # DbDocument (with extra_fields, search snippets/pages), DbType schemas
  ingest.ts                         # IngestResult schema
  review.ts                         # ReviewQueueItem (with extra_fields), ReviewPatch schemas
  schema.ts                         # SchemaInfo schemas
  audit.ts                          # AuditLogEntry, AuditLogPage schemas
  links.ts                          # DocumentLink, CreateLinkBody schemas
  access.ts                         # groups, admin users, grants (scope + permission level), integrity results
  versions.ts                       # document versions, field history, audit-chain verification
  sharing.ts                        # share links, export jobs, ExportRequest
  lifecycle.ts                      # lifecycle status, retention rules, legal holds, trash, destruction records
  collaboration.ts                  # notifications, preferences, lock, comments, signature requests / verification

packages/api-client/src/
  index.ts                          # all fetch functions, imageUrl helper, fetchAuditLog()

setup.sh                            # internet-connected machine: prepare offline bundle
install.sh                          # offline server: install from bundle + docker compose up
```

---

## Suggested Next Steps

> The GED extension (Phases 0-9) is complete - release 2.0.0 in CHANGELOG.md.

1. **Low-disk-space alert** on the Synchronisation page / admin notifications (today a manual check).
2. **Move from python-jose to PyJWT** (accepted CVEs only apply to asymmetric keys - see CHANGELOG).
3. **react-router v7** (two moderate advisories left in v6; the only dynamic navigate is guarded).
4. **Audit log user filter** - expose the `user_id` filter in `AuditPage` with a user email autocomplete.
5. **Bulk review** - select multiple documents in `ReviewPage` and approve/reject in batch.
6. **Tenant deactivate/delete flow** - see Known Limitations.
7. **VLM model switcher** for the legacy benchmark viewer.
