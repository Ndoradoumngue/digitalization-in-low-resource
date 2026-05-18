import { z } from "zod";

// ── Quality issues ────────────────────────────────────────────────────────────
// Model output is inconsistent ("handwriting" vs "handwriting_annotations"),
// so we keep z.string() at the schema level and enumerate known values separately.
export const KNOWN_QUALITY_ISSUES = [
  "flag_stripes",
  "handwriting",
  "handwriting_annotations",
  "upside_down",
  "faded_ink",
  "security_background",
  "physical_damage",
  "typewriter",
  "layered_documents",
] as const;
export type KnownQualityIssue = (typeof KNOWN_QUALITY_ISSUES)[number];

// ── Shared document fields (all nullable — real VLM output is unpredictable) ──
export const DocumentFieldsSchema = z.object({
  document_type: z.string().nullable(),
  reference_number: z.string().nullable(),
  date: z.string().nullable(),
  person_names: z.array(z.string()).nullable(),
  functions: z.array(z.string()).nullable(),
  destination_or_subject: z.string().nullable(),
  organisation: z.string().nullable(),
  signatory: z.string().nullable(),
  budget_line: z.string().nullable(),
  // Language comes back as "FR", "fr", "ar", "bilingual" — kept as string
  language: z.string().nullable(),
  bilingual_layout: z
    .enum(["parallel_columns", "sequential", "french_only", "arabic_only"])
    .nullable(),
  quality_issues: z.array(z.string()).nullable(),
});
export type DocumentFields = z.infer<typeof DocumentFieldsSchema>;

// High-confidence fields: document_type and quality_issues must be present.
// Extending constrains only those two — the rest remain nullable.
export const HighDocumentFieldsSchema = DocumentFieldsSchema.extend({
  document_type: z.string(),
  quality_issues: z.array(z.string()),
});
export type HighDocumentFields = z.infer<typeof HighDocumentFieldsSchema>;

// ── Confidence tier discriminated union ───────────────────────────────────────
//
// tier: "high"   → document_type guaranteed non-null, quality_issues non-null
// tier: "medium" → all fields present but may be null
// tier: "low"    → all fields nullable; model flagged uncertainty
// tier: "failed" → either a model/runtime error OR a JSON parse failure
//
export const HighResultSchema = z.object({
  tier: z.literal("high"),
  filename: z.string(),
  processing_time: z.number(),
  fields: HighDocumentFieldsSchema,
});

export const MediumResultSchema = z.object({
  tier: z.literal("medium"),
  filename: z.string(),
  processing_time: z.number(),
  fields: DocumentFieldsSchema,
});

export const LowResultSchema = z.object({
  tier: z.literal("low"),
  filename: z.string(),
  processing_time: z.number(),
  fields: DocumentFieldsSchema,
});

export const FailedResultSchema = z.object({
  tier: z.literal("failed"),
  filename: z.string(),
  processing_time: z.number(),
  error: z.string().optional(),
  raw_response: z.string().optional(),
  is_parse_error: z.boolean(),
});

export const ExtractionResultSchema = z.discriminatedUnion("tier", [
  HighResultSchema,
  MediumResultSchema,
  LowResultSchema,
  FailedResultSchema,
]);

export type ExtractionResult = z.infer<typeof ExtractionResultSchema>;
export type HighResult = z.infer<typeof HighResultSchema>;
export type MediumResult = z.infer<typeof MediumResultSchema>;
export type LowResult = z.infer<typeof LowResultSchema>;
export type FailedResult = z.infer<typeof FailedResultSchema>;
export type SuccessResult = HighResult | MediumResult | LowResult;

// ── Raw wire format (Python VLM script output) ────────────────────────────────
// Fields prefixed with _ are internal metadata added by the Python scripts.
// All document fields are optional since error / parse-error payloads omit them.
export const RawVlmResultSchema = z.object({
  // Document fields (present on success)
  document_type: z.string().nullish(),
  reference_number: z.string().nullish(),
  date: z.string().nullish(),
  person_names: z.array(z.string()).nullish(),
  functions: z.array(z.string()).nullish(),
  destination_or_subject: z.string().nullish(),
  organisation: z.string().nullish(),
  signatory: z.string().nullish(),
  budget_line: z.string().nullish(),
  language: z.string().nullish(),
  bilingual_layout: z.string().nullish(),
  quality_issues: z.array(z.string()).nullish(),
  extraction_confidence: z.enum(["high", "medium", "low"]).optional(),
  // Internal metadata always present
  _filename: z.string(),
  _time: z.number(),
  // Error / parse-error fields (mutually exclusive)
  _error: z.string().optional(),
  _parse_error: z.boolean().optional(),
  _raw: z.string().optional(),
});
export type RawVlmResult = z.infer<typeof RawVlmResultSchema>;

export const AllVlmResultsSchema = z.record(z.string(), RawVlmResultSchema);
export type AllVlmResults = z.infer<typeof AllVlmResultsSchema>;

// ── Parser: raw API payload → discriminated union ─────────────────────────────
export function parseVlmResult(raw: RawVlmResult): ExtractionResult {
  const { _filename, _time, _error, _parse_error, _raw, extraction_confidence, ...rest } =
    raw;

  if (_error || _parse_error) {
    return {
      tier: "failed",
      filename: _filename,
      processing_time: _time,
      ...(_error ? { error: _error } : {}),
      ...(_raw ? { raw_response: _raw } : {}),
      is_parse_error: Boolean(_parse_error),
    };
  }

  const tier: "high" | "medium" | "low" = extraction_confidence ?? "low";

  const fields: DocumentFields = {
    document_type: rest.document_type ?? null,
    reference_number: rest.reference_number ?? null,
    date: rest.date ?? null,
    person_names: rest.person_names ?? null,
    functions: rest.functions ?? null,
    destination_or_subject: rest.destination_or_subject ?? null,
    organisation: rest.organisation ?? null,
    signatory: rest.signatory ?? null,
    budget_line: rest.budget_line ?? null,
    language: rest.language ?? null,
    bilingual_layout: (rest.bilingual_layout as DocumentFields["bilingual_layout"]) ?? null,
    quality_issues: rest.quality_issues ?? null,
  };

  if (tier === "high") {
    return {
      tier: "high",
      filename: _filename,
      processing_time: _time,
      fields: {
        ...fields,
        document_type: fields.document_type ?? "unknown",
        quality_issues: fields.quality_issues ?? [],
      },
    };
  }

  return { tier, filename: _filename, processing_time: _time, fields };
}
