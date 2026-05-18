import { z } from "zod";

// ── Single engine result ──────────────────────────────────────────────────────
export const EngineResultSchema = z.object({
  text: z.string(),
  confidence: z.number(),
  time: z.number(),
  error: z.string().nullable(),
});
export type EngineResult = z.infer<typeof EngineResultSchema>;

// ── All three engines for one image variant ───────────────────────────────────
export const VariantResultSchema = z.object({
  tesseract: EngineResultSchema,
  easyocr: EngineResultSchema,
  surya: EngineResultSchema,
});
export type VariantResult = z.infer<typeof VariantResultSchema>;

// ── Full per-document OCR result ──────────────────────────────────────────────
export const OcrDocumentSchema = z.object({
  filename: z.string(),
  raw: VariantResultSchema,
  preprocessed: VariantResultSchema,
});
export type OcrDocument = z.infer<typeof OcrDocumentSchema>;

export const AllOcrResultsSchema = z.record(z.string(), OcrDocumentSchema);
export type AllOcrResults = z.infer<typeof AllOcrResultsSchema>;

// ── Document list entry ───────────────────────────────────────────────────────
export const DocumentEntrySchema = z.object({
  filename: z.string(),
  hasResult: z.boolean(),
});
export type DocumentEntry = z.infer<typeof DocumentEntrySchema>;

export const DocumentListSchema = z.array(DocumentEntrySchema);

// ── UI helpers ────────────────────────────────────────────────────────────────
export type ImageVariant = "raw" | "preprocessed";
export type EngineName = "tesseract" | "easyocr" | "surya";

export const ENGINE_LABELS: Record<EngineName, string> = {
  tesseract: "Tesseract",
  easyocr: "EasyOCR",
  surya: "Surya",
};
