import { z } from "zod";

export const DocumentStatusEnum = z.enum([
  "pending",
  "processing",
  "completed",
  "crashed",
  "out_of_scope",
  "review_required",
  "duplicate",
]);
export type DocumentStatus = z.infer<typeof DocumentStatusEnum>;

export const IngestDocumentSchema = z.object({
  id:              z.string(),
  filename:        z.string(),
  status:          DocumentStatusEnum,
  document_type:   z.string().nullable(),
  confidence:      z.string().nullable(),
  processing_time: z.number().nullable(),
  error_message:   z.string().nullable(),
  image_path:      z.string().nullable(),
});
export type IngestDocument = z.infer<typeof IngestDocumentSchema>;

export const BatchStatusSchema = z.object({
  batch_id:           z.string(),
  total:              z.number(),
  completed:          z.number(),
  crashed:            z.number(),
  review_required:    z.number(),
  out_of_scope:       z.number(),
  pending:            z.number(),
  duplicates_skipped: z.number(),
  documents:          z.array(IngestDocumentSchema),
});
export type BatchStatus = z.infer<typeof BatchStatusSchema>;

export const BatchCreatedSchema = z.object({ batch_id: z.string() });
export type BatchCreated = z.infer<typeof BatchCreatedSchema>;

export const PageStatusEnum = z.enum([
  "pending",
  "processing",
  "completed",
  "failed",
  "manual",
  "skipped",
]);
export type PageStatus = z.infer<typeof PageStatusEnum>;

export const QualityFlagSchema = z.object({
  code:  z.enum(["empty_value", "identical_values", "overlong_value", "repeated_items", "too_few_items"]),
  field: z.string(),
  count: z.number(),
});
export type QualityFlag = z.infer<typeof QualityFlagSchema>;

export const PageSchema = z.object({
  id:              z.string(),
  page_number:     z.number(),
  image_path:      z.string(),
  status:          PageStatusEnum,
  error_message:   z.string().nullable(),
  processing_time: z.number().nullable(),
  fields:          z.record(z.string(), z.unknown()).nullable(),
  quality_flags:   z.array(QualityFlagSchema).default([]),
  updated_at:      z.string(),
});
export type Page = z.infer<typeof PageSchema>;

export const PageListSchema = z.object({
  batch_document_id: z.string(),
  total:             z.number(),
  pending:           z.number(),
  processing:        z.number(),
  completed:         z.number(),
  failed:            z.number(),
  manual:            z.number(),
  skipped:           z.number(),
  flagged:           z.number().default(0),
  pages:             z.array(PageSchema),
});
export type PageList = z.infer<typeof PageListSchema>;

export const BatchSummarySchema = z.object({
  batch_id:           z.string(),
  source_type:        z.string(),
  created_at:         z.string(),
  total:              z.number(),
  completed:          z.number(),
  crashed:            z.number(),
  review_required:    z.number(),
  out_of_scope:       z.number(),
  pending:            z.number(),
  duplicates_skipped: z.number(),
});
export type BatchSummary = z.infer<typeof BatchSummarySchema>;
