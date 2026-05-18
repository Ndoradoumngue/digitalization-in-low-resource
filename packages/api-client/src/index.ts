import {
  AllOcrResultsSchema,
  AllVlmResultsSchema,
  DocumentListSchema,
  OcrDocumentSchema,
  RawVlmResultSchema,
  parseVlmResult,
  type AllOcrResults,
  type DocumentEntry,
  type ExtractionResult,
  type ImageVariant,
  type OcrDocument,
} from "@sdai/types";

// ── Core fetch helper ─────────────────────────────────────────────────────────
async function fetchJson<T>(
  url: string,
  schema: { parse: (data: unknown) => T }
): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return schema.parse(await res.json());
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
