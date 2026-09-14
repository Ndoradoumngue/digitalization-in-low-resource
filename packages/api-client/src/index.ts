import { z } from "zod";
import {
  AllOcrResultsSchema,
  AllVlmResultsSchema,
  AuditLogPageSchema,
  BatchCreatedSchema,
  BatchStatusSchema,
  BatchSummarySchema,
  DbDocumentDetailSchema,
  DbDocumentListSchema,
  DbSchemaSchema,
  DbTypesSchema,
  DocumentListSchema,
  LoginResponseSchema,
  OcrDocumentSchema,
  PageListSchema,
  RawVlmResultSchema,
  ReviewCountSchema,
  ReviewQueueSchema,
  UserSchema,
  parseVlmResult,
  type AllOcrResults,
  type AuditLogPage,
  type BatchCreated,
  type BatchStatus,
  type BatchSummary,
  type DbDocumentDetail,
  type DbDocumentList,
  type DbSchema,
  type DbType,
  type DocumentEntry,
  type ExtractionResult,
  type ImageVariant,
  type OcrDocument,
  type PageList,
  type ReviewCount,
  type ReviewQueue,
  type User,
} from "@sdai/types";

// ── Core fetch helper ─────────────────────────────────────────────────────────
// credentials: "include" ensures the httpOnly auth cookie is sent on every request.
async function fetchJson<T>(
  url: string,
  schema: { parse: (data: unknown) => T },
  init?: RequestInit
): Promise<T> {
  const res = await fetch(url, { credentials: "include", ...init });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return schema.parse(await res.json());
}

// ── Auth endpoints ────────────────────────────────────────────────────────────

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Login failed");
  }
  return LoginResponseSchema.parse(await res.json());
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}

// Returns null instead of throwing so AuthContext can handle the 401 silently.
export async function getMe(): Promise<User | null> {
  const res = await fetch("/api/auth/me", { credentials: "include" });
  if (res.status === 401) return null;
  if (!res.ok) return null;
  return UserSchema.parse(await res.json());
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

  const res = await fetch("/api/ingest/upload", {
    method: "POST",
    credentials: "include",
    body: fd,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return BatchCreatedSchema.parse(await res.json());
}

export async function ingestFromPath(
  payload: { path?: string; google_drive_folder_id?: string }
): Promise<BatchCreated> {
  const res = await fetch("/api/ingest/path", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
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
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/retry`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function reloadPage(pageId: string): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/reload`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function manualEnterPage(pageId: string, fields: Record<string, unknown>): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/manual`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function skipPage(pageId: string): Promise<void> {
  const res = await fetch(`/api/ingest/pages/${encodeURIComponent(pageId)}/skip`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

export async function resumeDocument(batchDocumentId: string): Promise<{ queued: number }> {
  const res = await fetch(`/api/ingest/pages/resume/${encodeURIComponent(batchDocumentId)}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteIngestedDocument(batchDocumentId: string): Promise<void> {
  const res = await fetch(`/api/ingest/documents/${encodeURIComponent(batchDocumentId)}`, {
    method: "DELETE",
    credentials: "include",
  });
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

export async function fetchDbTypes(): Promise<DbType[]> {
  return fetchJson("/api/db/types", DbTypesSchema);
}

export async function fetchDbSchema(): Promise<DbSchema> {
  return fetchJson("/api/db/schema", DbSchemaSchema);
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
    {
      method:      "PATCH",
      credentials: "include",
      headers:     { "Content-Type": "application/json" },
      body:        JSON.stringify(body),
    },
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
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function retryDocument(tableName: string, id: string): Promise<{ batch_id: string }> {
  const res = await fetch(
    `/api/review/${encodeURIComponent(tableName)}/${encodeURIComponent(id)}/retry`,
    { method: "POST", credentials: "include" },
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
    { method: "DELETE", credentials: "include" },
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
