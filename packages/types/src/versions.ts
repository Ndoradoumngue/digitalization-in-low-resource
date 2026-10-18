import { z } from "zod";

// ── File versions (GED Phase 2) ──────────────────────────────────────────────

export const DocumentVersionSchema = z.object({
  version_no:        z.number(),
  file_hash:         z.string().nullable(),
  size:              z.number().nullable(),
  mime:              z.string().nullable(),
  original_filename: z.string().nullable(),
  comment:           z.string().nullable(),
  // set when this version re-activated an earlier one's file
  restored_from:     z.number().nullable(),
  created_by:        z.string().nullable(),
  created_by_email:  z.string().nullable(),
  created_at:        z.string().nullable(),
  is_current:        z.boolean(),
});
export type DocumentVersion = z.infer<typeof DocumentVersionSchema>;

export const DocumentVersionsResponseSchema = z.object({
  current_version: z.number().nullable(),
  versions:        z.array(DocumentVersionSchema),
});
export type DocumentVersionsResponse = z.infer<typeof DocumentVersionsResponseSchema>;

export const VersionChangeResponseSchema = z.object({
  version:  DocumentVersionSchema,
  // re-extraction batch, pollable through GET /api/ingest/status/{batch_id}
  batch_id: z.string().nullable(),
});
export type VersionChangeResponse = z.infer<typeof VersionChangeResponseSchema>;

// ── Change history ───────────────────────────────────────────────────────────

export const CHANGE_SOURCES = ["edit", "review", "reextract", "sync"] as const;
export type ChangeSource = (typeof CHANGE_SOURCES)[number];

export const FieldChangeSchema = z.object({
  id:               z.string(),
  field:            z.string(),
  old_value:        z.unknown(),
  new_value:        z.unknown(),
  change_source:    z.enum(CHANGE_SOURCES),
  version_no:       z.number().nullable(),
  changed_by:       z.string().nullable(),
  changed_by_email: z.string().nullable(),
  created_at:       z.string(),
});
export type FieldChange = z.infer<typeof FieldChangeSchema>;

export const DocumentEventSchema = z.object({
  id:         z.string(),
  action:     z.string(),
  user_id:    z.string().nullable(),
  user_email: z.string().nullable(),
  details:    z.record(z.string(), z.unknown()).nullable(),
  created_at: z.string(),
});
export type DocumentEvent = z.infer<typeof DocumentEventSchema>;

export const DocumentHistoryResponseSchema = z.object({
  fields: z.array(FieldChangeSchema),
  events: z.array(DocumentEventSchema),
});
export type DocumentHistoryResponse = z.infer<typeof DocumentHistoryResponseSchema>;

// ── Audit chain / integrity ──────────────────────────────────────────────────

export const AuditChainBreakSchema = z
  .object({
    reason:      z.string(),
    origin_node: z.string().nullable().optional(),
    chain_seq:   z.number().nullable().optional(),
    id:          z.string().nullable().optional(),
  })
  .passthrough();
export type AuditChainBreak = z.infer<typeof AuditChainBreakSchema>;

export const AuditChainSummarySchema = z.object({
  ok:           z.boolean(),
  checked:      z.number(),
  first_broken: AuditChainBreakSchema.nullable(),
});
export type AuditChainSummary = z.infer<typeof AuditChainSummarySchema>;

export const AuditChainResultSchema = AuditChainSummarySchema.extend({
  chains: z.array(
    z.object({
      origin_node: z.string(),
      length:      z.number(),
      head_hash:   z.string().nullable(),
    }),
  ),
});
export type AuditChainResult = z.infer<typeof AuditChainResultSchema>;

// ── Extraction on demand (GED Phase 10) ──────────────────────────────────────

export const ExtractionStartedSchema = z.object({ batch_id: z.string() });
export type ExtractionStarted = z.infer<typeof ExtractionStartedSchema>;

export const ExtractionSelectionResponseSchema = z.object({
  results: z.array(
    z.object({
      table_name: z.string(),
      id:         z.string(),
      batch_id:   z.string().optional(),
      error:      z.string().optional(),
    }),
  ),
  started:   z.number(),
  batch_ids: z.array(z.string()),
});
export type ExtractionSelectionResponse = z.infer<typeof ExtractionSelectionResponseSchema>;

export const DocumentLocationSchema = z.object({ table_name: z.string() });
export type DocumentLocation = z.infer<typeof DocumentLocationSchema>;
