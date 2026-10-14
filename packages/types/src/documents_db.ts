import { z } from "zod";

// A run of snippet text; `hit` marks the words that matched the search.
// Rendered as text (never HTML) - see SearchSnippet.
export const SnippetSegmentSchema = z.object({
  text: z.string(),
  hit: z.boolean(),
});
export type SnippetSegment = z.infer<typeof SnippetSegmentSchema>;

export const SearchPageHitSchema = z.object({
  /** 1-based page number in the document viewer */
  page: z.number(),
  /** only the first few matching pages get one */
  snippet: z.array(SnippetSegmentSchema).nullable(),
});
export type SearchPageHit = z.infer<typeof SearchPageHitSchema>;

/** Present on results of a `q` search only. */
export const SearchDetailsSchema = z.object({
  /** extracted fields that matched (empty when only page text did) */
  snippet: z.array(SnippetSegmentSchema),
  /** pages whose text matched */
  pages: z.array(SearchPageHitSchema),
});
export type SearchDetails = z.infer<typeof SearchDetailsSchema>;

export const DbDocumentSchema = z.object({
  id: z.string(),
  table_name: z.string(),
  document_type: z.string().nullable(),
  record_id: z.string().nullable(),
  confidence: z.string().nullable(),
  review_status: z.string().nullable(),
  ingested_at: z.string().nullable(),
  source_image_path: z.string().nullable(),
  reviewed_by: z.string().nullable(),
  series_id: z.string().nullable(),
  // GED Phase 6 - archived / destruction_eligible documents are read-only.
  lifecycle_status: z.string().optional(),
  legal_hold: z.boolean().optional(),
  // Whatever non-system fields this document's own tenant/schema extracted -
  // opaque, since it varies per document type (see documents_router._extra_cols).
  extra_fields: z.record(z.string(), z.unknown()),
  search: SearchDetailsSchema.optional(),
});
export type DbDocument = z.infer<typeof DbDocumentSchema>;

export const DbDocumentListSchema = z.object({
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  results: z.array(DbDocumentSchema),
});
export type DbDocumentList = z.infer<typeof DbDocumentListSchema>;

export const DbTypeSchema = z.object({
  table_name: z.string(),
  document_type: z.string(),
  count: z.number(),
  last_ingested: z.string().nullable(),
});
export type DbType = z.infer<typeof DbTypeSchema>;

export const DbTypesSchema = z.array(DbTypeSchema);

// Detail returns all fields - strongly typed only for the common base set;
// the rest are opaque extra fields from the dynamic table.
export const DbDocumentDetailSchema = z.record(z.string(), z.unknown());
export type DbDocumentDetail = z.infer<typeof DbDocumentDetailSchema>;

export const ReviewerSchema = z.object({
  id: z.string(),
  email: z.string(),
  full_name: z.string().nullable(),
});
export type Reviewer = z.infer<typeof ReviewerSchema>;

export const ReviewersSchema = z.array(ReviewerSchema);
