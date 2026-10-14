import { z } from "zod";
import {
  AdminUsersResponseSchema,
  AllOcrResultsSchema,
  AllVlmResultsSchema,
  AuditLogPageSchema,
  BatchCreatedSchema,
  BatchStatusSchema,
  BatchSummarySchema,
  CreateAccessGrantResponseSchema,
  CreateGroupResponseSchema,
  CreateLinkResponseSchema,
  CreateSeriesResponseSchema,
  DbDocumentDetailSchema,
  DbDocumentListSchema,
  DbSchemaSchema,
  DbTypesSchema,
  DocumentAccessResponseSchema,
  DocumentLinksResponseSchema,
  DocumentListSchema,
  GranteesResponseSchema,
  GroupsResponseSchema,
  IntegrityCheckResultSchema,
  LoginResponseSchema,
  OcrDocumentSchema,
  PageListSchema,
  RawVlmResultSchema,
  ReviewCountSchema,
  ReviewQueueSchema,
  ReviewersSchema,
  SeriesListResponseSchema,
  UserSchema,
  parseVlmResult,
  type AdminUser,
  type AllOcrResults,
  type AuditLogPage,
  type BatchCreated,
  type BatchStatus,
  type BatchSummary,
  type DbDocumentDetail,
  type DbDocumentList,
  type DbSchema,
  type DbType,
  type DocumentAccessResponse,
  type DocumentEntry,
  type DocumentLinksResponse,
  type ExtractionResult,
  type GranteesResponse,
  type Group,
  type IntegrityCheckResult,
  type ImageVariant,
  type OcrDocument,
  type PageList,
  type ReviewCount,
  type ReviewQueue,
  type Reviewer,
  type Series,
  type User,
  ScopedGrantsResponseSchema,
  type ScopedGrantsResponse,
  AuditChainResultSchema,
  DocumentHistoryResponseSchema,
  DocumentVersionsResponseSchema,
  LastIntegrityRunSchema,
  VersionChangeResponseSchema,
  type AuditChainResult,
  type DocumentHistoryResponse,
  type DocumentVersionsResponse,
  type LastIntegrityRun,
  type VersionChangeResponse,
  type PermissionLevel,
  type Confidentiality,
  SyncConflictsResponseSchema,
  SyncFilesResponseSchema,
  SyncResolveResponseSchema,
  SyncStatusSchema,
  type SyncConflictsResponse,
  type SyncFilesResponse,
  type SyncResolution,
  type SyncResolveResponse,
  type SyncStatus,
  FilingRulesSchema,
  LinkSuggestionListSchema,
  TagListSchema,
  type FilingRules,
  type LinkSuggestion,
  type Tag,
  CreateShareResponseSchema,
  ExportJobSchema,
  ExportJobsResponseSchema,
  ShareListResponseSchema,
  type CreateShareResponse,
  type ExportJob,
  type ExportJobsResponse,
  type ExportRequest,
  type ShareListResponse,
} from "@sdai/types";

// ── Core fetch helper ─────────────────────────────────────────────────────────
// credentials: "include" ensures the httpOnly auth cookie is sent on every
// request. Accept-Language carries the user's chosen UI language (set by the
// language switcher, stored under the same localStorage key i18next's
// browser-language-detector uses) so the backend can localize error/response
// text - see apps/api/i18n.py.

function currentLocale(): string {
  try {
    return localStorage.getItem("sdai_lang") || "en";
  } catch {
    // localStorage can throw (private browsing, disabled storage) - English
    // is the backend's own default, so falling back to it here is a no-op.
    return "en";
  }
}

function withAuth(init: RequestInit = {}): RequestInit {
  return {
    ...init,
    credentials: "include",
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      "Accept-Language": currentLocale(),
    },
  };
}

async function fetchJson<T>(
  url: string,
  schema: { parse: (data: unknown) => T },
  init?: RequestInit
): Promise<T> {
  const res = await fetch(url, withAuth(init));
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return schema.parse(await res.json());
}

// ── Auth endpoints ────────────────────────────────────────────────────────────

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch("/api/auth/login", withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Login failed");
  }
  return LoginResponseSchema.parse(await res.json());
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", withAuth({ method: "POST" }));
}

// Returns null instead of throwing so AuthContext can handle the 401 silently.
export async function getMe(): Promise<User | null> {
  const res = await fetch("/api/auth/me", withAuth());
  if (res.status === 401) return null;
  if (!res.ok) return null;
  try {
    return UserSchema.parse(await res.json());
  } catch (e) {
    // A stale tab open across a deploy that changed this shape would
    // otherwise throw here uncaught - AuthContext's getMe().then(setUser)
    // would never fire, silently stranding the app in its initial
    // logged-out state instead of falling back cleanly like the 401 case.
    console.error("getMe(): /api/auth/me response failed schema validation", e);
    return null;
  }
}

// ── OCR endpoints ─────────────────────────────────────────────────────────────

export async function fetchDocuments(): Promise<DocumentEntry[]> {
  return fetchJson("/api/documents", DocumentListSchema);
}

export async function fetchOcrResult(filename: string): Promise<OcrDocument> {
  return fetchJson(
    `/api/documents/${encodeURIComponent(filename)}`,
    OcrDocumentSchema
  );
}

export async function fetchAllOcrResults(): Promise<AllOcrResults> {
  return fetchJson("/api/results", AllOcrResultsSchema);
}

// ── VLM endpoints ─────────────────────────────────────────────────────────────

export async function fetchVlmResult(filename: string): Promise<ExtractionResult> {
  const raw = await fetchJson(
    `/api/vlm/documents/${encodeURIComponent(filename)}`,
    RawVlmResultSchema
  );
  return parseVlmResult(raw);
}

export async function fetchAllVlmResults(): Promise<Record<string, ExtractionResult>> {
  const rawMap = await fetchJson("/api/vlm/results", AllVlmResultsSchema);
  return Object.fromEntries(
    Object.entries(rawMap).map(([k, v]) => [k, parseVlmResult(v)])
  );
}

export async function fetchVlmDocuments(): Promise<DocumentEntry[]> {
  return fetchJson("/api/vlm/documents", DocumentListSchema);
}

// ── Image URL helpers ─────────────────────────────────────────────────────────

export function imageUrl(filename: string, variant: ImageVariant): string {
  return `/images/${variant}/${encodeURIComponent(filename)}`;
}

// ── Ingest endpoints ──────────────────────────────────────────────────────────

export async function uploadFiles(files: File[]): Promise<BatchCreated> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);

  const res = await fetch("/api/ingest/upload", withAuth({
    method: "POST",
    body: fd,
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return BatchCreatedSchema.parse(await res.json());
}

export async function ingestFromPath(
  payload: { path?: string; google_drive_folder_id?: string }
): Promise<BatchCreated> {
  const res = await fetch("/api/ingest/path", withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return BatchCreatedSchema.parse(await res.json());
}

export async function fetchBatchStatus(batchId: string): Promise<BatchStatus> {
  return fetchJson(`/api/ingest/status/${encodeURIComponent(batchId)}`, BatchStatusSchema);
}

export async function fetchRecentBatches(limit = 20): Promise<BatchSummary[]> {
  return fetchJson(`/api/ingest/batches?limit=${limit}`, z.array(BatchSummarySchema));
}

export async function deleteBatch(batchId: string): Promise<void> {
  const res = await fetch(`/api/ingest/batches/${encodeURIComponent(batchId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function fetchPageStatus(batchDocumentId: string): Promise<PageList> {
  return fetchJson(`/api/ingest/pages/${encodeURIComponent(batchDocumentId)}`, PageListSchema);
}

export async function retryPage(pageId: string): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/retry`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function reloadPage(pageId: string): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/reload`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function manualEnterPage(pageId: string, fields: Record<string, unknown>): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/manual`, withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function skipPage(pageId: string): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/skip`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function cancelPage(pageId: string): Promise<{ cancelled: boolean }> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/cancel`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function pauseDocument(batchDocumentId: string): Promise<{ cancelled: boolean }> {
  const res = await fetch(`/api/ingest/documents/${encodeURIComponent(batchDocumentId)}/pause`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function resumeDocument(
  batchDocumentId: string
): Promise<{ queued: number; already_running: boolean }> {
  const res = await fetch(`/api/ingest/pages/resume/${encodeURIComponent(batchDocumentId)}`, withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteIngestedDocument(batchDocumentId: string): Promise<void> {
  const res = await fetch(`/api/ingest/documents/${encodeURIComponent(batchDocumentId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Document DB endpoints ─────────────────────────────────────────────────────

export interface DbListParams {
  page?: number;
  page_size?: number;
  document_type?: string;
  confidence?: string;
  review_status?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
  reviewed_by?: string;
  series?: string;
  /** with `series`: also every series below it */
  include_subseries?: boolean;
  tag?: string;
}

export async function fetchDbDocuments(params: DbListParams = {}): Promise<DbDocumentList> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
  }
  return fetchJson(`/api/db/documents?${qs}`, DbDocumentListSchema);
}

export async function fetchDbDocumentDetail(
  tableName: string,
  id: string,
): Promise<DbDocumentDetail> {
  return fetchJson(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}`,
    DbDocumentDetailSchema,
  );
}

export async function updateDocumentFields(
  tableName: string,
  id: string,
  fields: Record<string, unknown>,
): Promise<DbDocumentDetail> {
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/fields`,
    withAuth({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return DbDocumentDetailSchema.parse(await res.json());
}

export async function fetchDbTypes(): Promise<DbType[]> {
  return fetchJson("/api/db/types", DbTypesSchema);
}

export async function fetchDbReviewers(): Promise<Reviewer[]> {
  return fetchJson("/api/db/reviewers", ReviewersSchema);
}

export async function fetchDbSchema(): Promise<DbSchema> {
  return fetchJson("/api/db/schema", DbSchemaSchema);
}

// ── Document links (chain-of-custody) ─────────────────────────────────────────

export interface CreateLinkBody {
  to_table: string;
  to_id: string;
  relation: string;
  note?: string;
}

export async function fetchDocumentLinks(
  tableName: string,
  id: string,
): Promise<DocumentLinksResponse> {
  return fetchJson(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/links`,
    DocumentLinksResponseSchema,
  );
}

export async function createDocumentLink(
  tableName: string,
  id: string,
  body: CreateLinkBody,
): Promise<{ id: string }> {
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/links`,
    withAuth({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return CreateLinkResponseSchema.parse(await res.json());
}

export async function deleteDocumentLink(linkId: string): Promise<void> {
  const res = await fetch(`/api/db/links/${encodeURIComponent(linkId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Document access grants ────────────────────────────────────────────────────

export async function fetchGrantees(): Promise<GranteesResponse> {
  return fetchJson("/api/db/grantees", GranteesResponseSchema);
}

export async function fetchDocumentAccess(
  tableName: string,
  id: string,
): Promise<DocumentAccessResponse> {
  return fetchJson(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/access`,
    DocumentAccessResponseSchema,
  );
}

export interface CreateAccessGrantBody {
  grantee_type: "group" | "user";
  grantee_id: string;
  permission?: PermissionLevel;
}

export async function createDocumentAccess(
  tableName: string,
  id: string,
  body: CreateAccessGrantBody,
): Promise<{ id: string }> {
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/access`,
    withAuth({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return CreateAccessGrantResponseSchema.parse(await res.json());
}

export async function deleteDocumentAccess(grantId: string): Promise<void> {
  const res = await fetch(`/api/db/access/${encodeURIComponent(grantId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

async function sendJson(url: string, method: string, body?: unknown): Promise<Response> {
  const res = await fetch(url, withAuth({
    method,
    ...(body !== undefined
      ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : {}),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res;
}

export async function updateAccessGrant(grantId: string, permission: PermissionLevel): Promise<void> {
  await sendJson(`/api/db/access/${encodeURIComponent(grantId)}`, "PATCH", { permission });
}

/** Series-level or document-type-level grants of this tenant (admin only). */
export async function fetchScopedGrants(scope: "series" | "doc_type"): Promise<ScopedGrantsResponse> {
  return fetchJson(`/api/db/grants?scope=${scope}`, ScopedGrantsResponseSchema);
}

export interface CreateScopedGrantBody extends CreateAccessGrantBody {
  scope: "series" | "doc_type";
  series_id?: string;
  table_name?: string;
}

export async function createScopedGrant(body: CreateScopedGrantBody): Promise<{ id: string }> {
  const res = await sendJson("/api/db/grants", "POST", body);
  return CreateAccessGrantResponseSchema.parse(await res.json());
}

export async function setDocumentConfidentiality(
  tableName: string,
  id: string,
  confidentiality: Confidentiality,
): Promise<void> {
  await sendJson(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/confidentiality`,
    "PATCH",
    { confidentiality },
  );
}

// ── Fonds/series hierarchy ─────────────────────────────────────────────────────

export async function fetchSeries(): Promise<Series[]> {
  return fetchJson("/api/db/series", SeriesListResponseSchema);
}

export interface SeriesInput {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  code?: string | null;
  position?: number;
}

export async function createSeries(
  name: string,
  description?: string,
  extra: Omit<SeriesInput, "name" | "description"> = {},
): Promise<{ id: string; name: string }> {
  const res = await fetch("/api/db/series", withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, ...extra }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return CreateSeriesResponseSchema.parse(await res.json());
}

/** Rename / recode / reorder / move (parent_id null = to the root). Only
 * the keys present are changed. */
export async function updateSeries(seriesId: string, changes: Partial<SeriesInput>): Promise<void> {
  await sendJson(`/api/db/series/${encodeURIComponent(seriesId)}`, "PATCH", changes);
}

export async function deleteSeries(seriesId: string): Promise<void> {
  const res = await fetch(`/api/db/series/${encodeURIComponent(seriesId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function assignDocumentSeries(
  tableName: string,
  id: string,
  seriesId: string | null,
): Promise<void> {
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/series`,
    withAuth({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series_id: seriesId }),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Review queue endpoints ────────────────────────────────────────────────────

export interface ReviewQueueParams {
  page?: number;
  page_size?: number;
  document_type?: string;
}

export interface ReviewPatchBody {
  // Scalars, arrays of scalars, or - for list-of-objects fields such as a
  // lexicon's "entries" - the parsed JSON value itself.
  fields: Record<string, unknown>;
  action: "approve" | "reject";
}

export async function fetchReviewQueue(params: ReviewQueueParams = {}): Promise<ReviewQueue> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
  }
  return fetchJson(`/api/review/queue?${qs}`, ReviewQueueSchema);
}

export async function fetchReviewCount(): Promise<ReviewCount> {
  return fetchJson("/api/review/count", ReviewCountSchema);
}

export async function patchReview(
  tableName: string,
  id: string,
  body: ReviewPatchBody,
): Promise<DbDocumentDetail> {
  const res = await fetch(
    `/api/review/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}`,
    withAuth({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return DbDocumentDetailSchema.parse(await res.json());
}

export async function flagReview(tableName: string, id: string): Promise<void> {
  const res = await fetch(
    `/api/review/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/flag`,
    withAuth({ method: "POST" }),
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function retryDocument(tableName: string, id: string): Promise<{ batch_id: string }> {
  const res = await fetch(
    `/api/review/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/retry`,
    withAuth({ method: "POST" }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteDocument(tableName: string, id: string): Promise<void> {
  const res = await fetch(
    `/api/review/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}`,
    withAuth({ method: "DELETE" }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

/** URL of a pipeline file (page image, original PDF). Pass the document it
 * belongs to whenever it is known - the server then checks that document's
 * access directly instead of tracing the file back to its upload. */
export function dbImageUrl(sourcePath: string, doc?: { table: string; id: string }): string {
  const base = `/api/db/image?path=${encodeURIComponent(sourcePath)}`;
  return doc
    ? `${base}&table=${encodeURIComponent(doc.table)}&id=${encodeURIComponent(doc.id)}`
    : base;
}

// ── Admin endpoints ───────────────────────────────────────────────────────────

export interface AuditLogParams {
  page?: number;
  page_size?: number;
  user_id?: string;
  action?: string;
  date_from?: string;
  date_to?: string;
}

export async function fetchAuditLog(params: AuditLogParams = {}): Promise<AuditLogPage> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
  }
  return fetchJson(`/api/admin/audit-log?${qs}`, AuditLogPageSchema);
}

// ── Admin: groups ─────────────────────────────────────────────────────────────

export async function fetchGroups(): Promise<Group[]> {
  return fetchJson("/api/admin/groups", GroupsResponseSchema);
}

export async function createGroup(name: string): Promise<{ id: string; name: string }> {
  const res = await fetch("/api/admin/groups", withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return CreateGroupResponseSchema.parse(await res.json());
}

export async function deleteGroup(groupId: string): Promise<void> {
  const res = await fetch(`/api/admin/groups/${encodeURIComponent(groupId)}`, withAuth({
    method: "DELETE",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Admin: users ──────────────────────────────────────────────────────────────

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  return fetchJson("/api/admin/users", AdminUsersResponseSchema);
}

export async function updateUserAccessManager(
  userId: string,
  canManageAccess: boolean,
): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, withAuth({
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ can_manage_access: canManageAccess }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function updateUserExtractionEditor(
  userId: string,
  canEditExtraction: boolean,
): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, withAuth({
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ can_edit_extraction: canEditExtraction }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export interface UpdateUserBody {
  role?: "reviewer" | "reader";
  can_manage_access?: boolean;
  can_edit_extraction?: boolean;
  can_manage_lifecycle?: boolean;
  can_share?: boolean;
  clearance?: Confidentiality;
}

/** Partial update of a user's role, delegated flags or clearance (admin only). */
export async function updateAdminUser(userId: string, body: UpdateUserBody): Promise<void> {
  await sendJson(`/api/admin/users/${encodeURIComponent(userId)}`, "PATCH", body);
}

export async function addUserToGroup(userId: string, groupId: string): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/groups`, withAuth({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_id: groupId }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function removeUserFromGroup(userId: string, groupId: string): Promise<void> {
  const res = await fetch(
    `/api/admin/users/${encodeURIComponent(userId)}/groups/${encodeURIComponent(groupId)}`,
    withAuth({ method: "DELETE" }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Admin: fixity / integrity check ──────────────────────────────────────────

export async function runIntegrityCheck(): Promise<IntegrityCheckResult> {
  const res = await fetch("/api/admin/integrity-check", withAuth({
    method: "POST",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return IntegrityCheckResultSchema.parse(await res.json());
}

export async function fetchLastIntegrityRun(): Promise<LastIntegrityRun> {
  return fetchJson("/api/admin/integrity", LastIntegrityRunSchema);
}

export async function verifyAuditChain(): Promise<AuditChainResult> {
  return fetchJson("/api/admin/integrity/audit-chain", AuditChainResultSchema);
}

// ── Document versions and history ────────────────────────────────────────────

function documentPath(tableName: string, id: string): string {
  return `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}`;
}

async function jsonOrThrow<T>(res: Response, schema: { parse: (data: unknown) => T }): Promise<T> {
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return schema.parse(await res.json());
}

export async function fetchDocumentVersions(
  tableName: string,
  id: string,
): Promise<DocumentVersionsResponse> {
  return fetchJson(`${documentPath(tableName, id)}/versions`, DocumentVersionsResponseSchema);
}

export interface VersionChangeOptions {
  comment?: string;
  /** Also run the file through extraction into this same document. */
  reextract?: boolean;
}

export async function uploadDocumentVersion(
  tableName: string,
  id: string,
  file: File,
  { comment, reextract = false }: VersionChangeOptions = {},
): Promise<VersionChangeResponse> {
  const fd = new FormData();
  fd.append("file", file);
  if (comment) fd.append("comment", comment);
  fd.append("reextract", String(reextract));
  const res = await fetch(`${documentPath(tableName, id)}/versions`, withAuth({ method: "POST", body: fd }));
  return jsonOrThrow(res, VersionChangeResponseSchema);
}

export async function restoreDocumentVersion(
  tableName: string,
  id: string,
  versionNo: number,
  { comment, reextract = false }: VersionChangeOptions = {},
): Promise<VersionChangeResponse> {
  const res = await fetch(
    `${documentPath(tableName, id)}/versions/${versionNo}/restore`,
    withAuth({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comment: comment || null, reextract }),
    }),
  );
  return jsonOrThrow(res, VersionChangeResponseSchema);
}

/** URL of one version's file - `inline` opens it in the browser instead of
 * downloading it. Either way the server logs a document_downloaded event. */
export function documentVersionFileUrl(
  tableName: string,
  id: string,
  versionNo: number,
  inline = false,
): string {
  return `${documentPath(tableName, id)}/versions/${versionNo}/file${inline ? "?inline=true" : ""}`;
}

/** URL of the document's original file - the current version, or
 * `versionNo`. Named "<record id>_<original name>"; every download is
 * audited (and may be watermarked for confidential PDFs). */
export function documentDownloadUrl(
  tableName: string,
  id: string,
  versionNo?: number | null,
  inline = false,
): string {
  const params = new URLSearchParams();
  if (versionNo != null) params.set("version", String(versionNo));
  if (inline) params.set("inline", "true");
  const query = params.toString();
  return `${documentPath(tableName, id)}/download${query ? `?${query}` : ""}`;
}

/** The file name a Content-Disposition header gives - the exact UTF-8
 * filename* (RFC 5987) when present, else the plain filename. */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      // malformed escape - fall through to the plain name
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain?.[1] ?? fallback;
}

/** Fetch a file (original download, export ZIP) so an error - e.g. "still
 * being synchronised" - can be shown instead of a raw JSON page. */
export async function downloadFile(url: string, fallbackName = "download"): Promise<ArchiveExportResult> {
  const res = await fetch(url, withAuth({ method: "GET" }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return {
    blob: await res.blob(),
    filename: filenameFromDisposition(res.headers.get("content-disposition"), fallbackName),
  };
}

// ── External share links ─────────────────────────────────────────────────────

export async function fetchDocumentShares(tableName: string, id: string): Promise<ShareListResponse> {
  return fetchJson(`${documentPath(tableName, id)}/shares`, ShareListResponseSchema);
}

export interface CreateShareBody {
  expires_in_days: number;
  password?: string;
  max_downloads?: number;
  /** pin a version - omitted: always the current one */
  version_no?: number;
  note?: string;
}

export async function createDocumentShare(
  tableName: string,
  id: string,
  body: CreateShareBody,
): Promise<CreateShareResponse> {
  const res = await sendJson(`${documentPath(tableName, id)}/shares`, "POST", body);
  return jsonOrThrow(res, CreateShareResponseSchema);
}

export async function revokeDocumentShare(tableName: string, id: string, shareId: string): Promise<void> {
  await sendJson(`${documentPath(tableName, id)}/shares/${encodeURIComponent(shareId)}`, "DELETE");
}

// ── Selective export ─────────────────────────────────────────────────────────

export async function createExport(body: ExportRequest): Promise<ExportJob> {
  const res = await sendJson("/api/db/export", "POST", body);
  return jsonOrThrow(res, ExportJobSchema);
}

export async function fetchExportJobs(): Promise<ExportJobsResponse> {
  return fetchJson("/api/db/export/jobs", ExportJobsResponseSchema);
}

export async function deleteExportJob(jobId: string): Promise<void> {
  await sendJson(`/api/db/export/jobs/${encodeURIComponent(jobId)}`, "DELETE");
}

/** The finished ZIP (audited as export_downloaded). */
export function exportJobDownloadUrl(jobId: string): string {
  return `/api/db/export/jobs/${encodeURIComponent(jobId)}/download`;
}

export async function fetchDocumentHistory(
  tableName: string,
  id: string,
): Promise<DocumentHistoryResponse> {
  return fetchJson(`${documentPath(tableName, id)}/history`, DocumentHistoryResponseSchema);
}

// ── Document export ──────────────────────────────────────────────────────────

export type DocumentExportFormat = "json" | "sql" | "csv";

/** One document's data as a file. "csv" exports one list field (e.g.
 *  "entries") as a spreadsheet and needs `field`. */
export async function exportDocument(
  tableName: string,
  id: string,
  format: DocumentExportFormat,
  field?: string,
): Promise<ArchiveExportResult> {
  const params = new URLSearchParams({ format });
  if (field) params.set("field", field);
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/export?${params}`,
    withAuth({ method: "GET" }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  return { blob: await res.blob(), filename: match?.[1] ?? `${tableName}_${id}.${format}` };
}

// ── Admin: archive export ────────────────────────────────────────────────────

export interface ArchiveExportResult {
  blob: Blob;
  filename: string;
}

export async function exportArchive(format: "json" | "sql"): Promise<ArchiveExportResult> {
  const res = await fetch(`/api/admin/export?format=${format}`, withAuth({
    method: "GET",
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? `archive_export_${format}.zip`;
  return { blob: await res.blob(), filename };
}

// ── Admin: local/cloud sync and backups ──────────────────────────────────────

export async function fetchSyncStatus(): Promise<SyncStatus> {
  return fetchJson("/api/admin/sync/status", SyncStatusSchema);
}

export async function fetchSyncConflicts(
  status: "open" | "resolved" = "open",
): Promise<SyncConflictsResponse> {
  return fetchJson(`/api/admin/sync/conflicts?status=${status}`, SyncConflictsResponseSchema);
}

export async function resolveSyncConflict(
  id: string,
  action: SyncResolution,
  value?: unknown,
): Promise<SyncResolveResponse> {
  const res = await fetch(
    `/api/admin/sync/conflicts/${encodeURIComponent(id)}/resolve`,
    withAuth({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, value: value ?? null }),
    }),
  );
  return jsonOrThrow(res, SyncResolveResponseSchema);
}

export async function fetchSyncProblemFiles(): Promise<SyncFilesResponse> {
  return fetchJson("/api/admin/sync/files", SyncFilesResponseSchema);
}

export async function retrySyncFiles(): Promise<{ retried: number }> {
  const res = await fetch("/api/admin/sync/files/retry", withAuth({ method: "POST" }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as { retried: number };
}

// ── Classification: tags, filing rules, link suggestions ─────────────────────

function docPath(tableName: string, id: string): string {
  return `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}`;
}

export async function fetchTags(): Promise<Tag[]> {
  return fetchJson("/api/db/tags", TagListSchema);
}

export async function fetchDocumentTags(tableName: string, id: string): Promise<Tag[]> {
  return fetchJson(`${docPath(tableName, id)}/tags`, TagListSchema);
}

/** Tag a document with an existing tag (`tagId`) or by name (an unknown
 * name creates the tag). Returns the document's tags. */
export async function addDocumentTag(
  tableName: string,
  id: string,
  tag: { tagId?: string; name?: string },
): Promise<Tag[]> {
  return TagListSchema.parse(
    await (
      await sendJson(`${docPath(tableName, id)}/tags`, "POST", { tag_id: tag.tagId ?? null, name: tag.name ?? null })
    ).json(),
  );
}

export async function removeDocumentTag(tableName: string, id: string, tagId: string): Promise<Tag[]> {
  return TagListSchema.parse(
    await (await sendJson(`${docPath(tableName, id)}/tags/${encodeURIComponent(tagId)}`, "DELETE")).json(),
  );
}

export async function createTag(name: string, color?: string | null): Promise<Tag> {
  return TagListSchema.element.parse(
    await (await sendJson("/api/db/tags", "POST", { name, color: color ?? null })).json(),
  );
}

export async function updateTag(tagId: string, changes: { name?: string; color?: string | null }): Promise<void> {
  await sendJson(`/api/db/tags/${encodeURIComponent(tagId)}`, "PATCH", changes);
}

export async function mergeTag(tagId: string, intoId: string): Promise<void> {
  await sendJson(`/api/db/tags/${encodeURIComponent(tagId)}/merge`, "POST", { into_id: intoId });
}

export async function deleteTag(tagId: string): Promise<void> {
  await sendJson(`/api/db/tags/${encodeURIComponent(tagId)}`, "DELETE");
}

export async function fetchFilingRules(): Promise<FilingRules> {
  return fetchJson("/api/db/filing-rules", FilingRulesSchema);
}

export async function setFilingRule(tableName: string, seriesId: string | null): Promise<void> {
  await sendJson(`/api/db/filing-rules/${encodeURIComponent(tableName)}`, "PUT", { series_id: seriesId });
}

export async function fetchLinkSuggestions(tableName: string, id: string): Promise<LinkSuggestion[]> {
  return fetchJson(`${docPath(tableName, id)}/link-suggestions`, LinkSuggestionListSchema);
}

