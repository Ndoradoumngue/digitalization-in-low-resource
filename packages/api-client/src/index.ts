import {
  AllOcrResultsSchema,
  AllVlmResultsSchema,
  DocumentListSchema,
  LoginResponseSchema,
  OcrDocumentSchema,
  RawVlmResultSchema,
  UserSchema,
  parseVlmResult,
  type AllOcrResults,
  type DocumentEntry,
  type ExtractionResult,
  type ImageVariant,
  type OcrDocument,
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

export async function login(username: string, password: string): Promise<User> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
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
