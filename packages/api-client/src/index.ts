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
} from "@sdai/types";

// ── Core fetch helper ─────────────────────────────────────────────────────────
// credentials: "include" ensures the httpOnly auth cookie is sent on every
// request. Accept-Language carries the user's chosen UI language (set by the
// language switcher, stored under the same localStorage key i18next's
// browser-language-detector uses) so the backend can localize error/response
// text — see apps/api/i18n.py.

function currentLocale(): string {
  try {
    return localStorage.getItem("sdai_lang") || "en";
  } catch {
    // localStorage can throw (private browsing, disabled storage) — English
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
    // otherwise throw here uncaught — AuthContext's getMe().then(setUser)
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

export async function resumeDocument(batchDocumentId: string): Promise<{ queued: number }> {
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
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ fields }),
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
  to_id:    string;
  relation: string;
  note?:    string;
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
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
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
  grantee_id:   string;
}

export async function createDocumentAccess(
  tableName: string,
  id: string,
  body: CreateAccessGrantBody,
): Promise<{ id: string }> {
  const res = await fetch(
    `/api/db/documents/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/access`,
    withAuth({
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
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

// ── Fonds/series hierarchy ─────────────────────────────────────────────────────

export async function fetchSeries(): Promise<Series[]> {
  return fetchJson("/api/db/series", SeriesListResponseSchema);
}

export async function createSeries(
  name: string,
  description?: string,
): Promise<{ id: string; name: string }> {
  const res = await fetch("/api/db/series", withAuth({
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ name, description }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return CreateSeriesResponseSchema.parse(await res.json());
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
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ series_id: seriesId }),
    }),
  );
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

// ── Review queue endpoints ────────────────────────────────────────────────────

export interface ReviewQueueParams {
  page?:          number;
  page_size?:     number;
  document_type?: string;
}

export interface ReviewPatchBody {
  fields: Record<string, string | string[] | null>;
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
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
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

export function dbImageUrl(sourcePath: string): string {
  return `/api/db/image?path=${encodeURIComponent(sourcePath)}`;
}

// ── Admin endpoints ───────────────────────────────────────────────────────────

export interface AuditLogParams {
  page?:      number;
  page_size?: number;
  user_id?:   string;
  action?:    string;
  date_from?: string;
  date_to?:   string;
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
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ name }),
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
    method:  "PATCH",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ can_manage_access: canManageAccess }),
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
    method:  "PATCH",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ can_edit_extraction: canEditExtraction }),
  }));
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function addUserToGroup(userId: string, groupId: string): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/groups`, withAuth({
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ group_id: groupId }),
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

// ── Admin: archive export ────────────────────────────────────────────────────

export interface ArchiveExportResult {
  blob:     Blob;
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
  const match        = disposition.match(/filename="?([^"]+)"?/);
  const filename      = match?.[1] ?? `archive_export_${format}.zip`;
  return { blob: await res.blob(), filename };
}
