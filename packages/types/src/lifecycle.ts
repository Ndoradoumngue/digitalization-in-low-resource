import { z } from "zod";

// ── Lifecycle, trash, retention, legal hold, destruction (GED Phase 6) ──────

export const LIFECYCLE_STATUSES = ["active", "archived", "destruction_eligible", "destroyed"] as const;
export type LifecycleStatus = (typeof LIFECYCLE_STATUSES)[number];

export const RETENTION_TRIGGERS = ["document_date", "ingested_at", "reviewed_at", "archived_at"] as const;
export type RetentionTrigger = (typeof RETENTION_TRIGGERS)[number];

export const FINAL_ACTIONS = ["destroy", "keep_forever", "review"] as const;
export type FinalAction = (typeof FINAL_ACTIONS)[number];

export const LIFECYCLE_ACTIONS = ["archive", "trash", "restore", "extend", "hold", "release_hold"] as const;
export type LifecycleAction = (typeof LIFECYCLE_ACTIONS)[number];

export const RetentionRuleSchema = z.object({
  id:              z.string(),
  name:            z.string(),
  scope:           z.enum(["series", "doc_type"]),
  series_id:       z.string().nullable(),
  series_name:     z.string().nullable(),
  series_code:     z.string().nullable(),
  table_name:      z.string().nullable(),
  duration_months: z.number(),
  trigger_on:      z.enum(RETENTION_TRIGGERS),
  date_field:      z.string().nullable(),
  final_action:    z.enum(FINAL_ACTIONS),
  created_at:      z.string().nullable(),
  document_count:  z.number().optional(),
});
export type RetentionRule = z.infer<typeof RetentionRuleSchema>;
export const RetentionRuleListSchema = z.array(RetentionRuleSchema);

export interface RetentionRuleBody {
  name: string;
  scope: "series" | "doc_type";
  series_id?: string;
  table_name?: string;
  duration_months: number;
  trigger_on: RetentionTrigger;
  date_field?: string;
  final_action: FinalAction;
}

export const LegalHoldSchema = z.object({
  id:             z.string(),
  scope:          z.enum(["documents", "series"]),
  origin:         z.enum(["document", "series", "search"]),
  series_id:      z.string().nullable().optional(),
  series_name:    z.string().nullable(),
  query:          z.record(z.string(), z.unknown()).nullable().optional(),
  reason:         z.string(),
  created_at:     z.string().nullable(),
  created_by:     z.string().nullable(),
  released_at:    z.string().nullable().optional(),
  released_by:    z.string().nullable().optional(),
  release_reason: z.string().nullable().optional(),
  document_count: z.number().optional(),
});
export type LegalHold = z.infer<typeof LegalHoldSchema>;
export const LegalHoldListSchema = z.array(LegalHoldSchema);

export const DocumentLifecycleSchema = z.object({
  lifecycle_status:   z.enum(LIFECYCLE_STATUSES),
  archived_at:        z.string().nullable(),
  retention_until:    z.string().nullable(),
  retention_override: z.string().nullable(),
  retention_rule:     z
    .object({
      id:              z.string(),
      name:            z.string(),
      scope:           z.string(),
      duration_months: z.number(),
      trigger_on:      z.string(),
      final_action:    z.string(),
    })
    .nullable(),
  legal_hold:     z.boolean(),
  holds:          z.array(LegalHoldSchema),
  in_trash:       z.boolean(),
  deleted_at:     z.string().nullable(),
  deleted_reason: z.string().nullable(),
  purge_at:       z.string().nullable(),
  actions:        z.array(z.enum(LIFECYCLE_ACTIONS)),
});
export type DocumentLifecycle = z.infer<typeof DocumentLifecycleSchema>;

export const TrashItemSchema = z.object({
  table_name:       z.string(),
  id:               z.string(),
  record_id:        z.string().nullable(),
  document_type:    z.string().nullable(),
  display:          z.string().nullable(),
  lifecycle_status: z.string(),
  deleted_at:       z.string(),
  deleted_by:       z.string().nullable(),
  deleted_reason:   z.string().nullable(),
  purge_at:         z.string().nullable(),
  restorable:       z.boolean(),
  legal_hold:       z.boolean(),
});
export type TrashItem = z.infer<typeof TrashItemSchema>;

export const TrashPageSchema = z.object({
  items:          z.array(TrashItemSchema),
  total:          z.number(),
  page:           z.number(),
  page_size:      z.number(),
  retention_days: z.number(),
});
export type TrashPage = z.infer<typeof TrashPageSchema>;

export const EligibleDocumentSchema = z.object({
  table_name:             z.string(),
  id:                     z.string(),
  record_id:              z.string().nullable(),
  document_type:          z.string().nullable(),
  display:                z.string().nullable(),
  retention_until:        z.string().nullable(),
  archived_at:            z.string().nullable(),
  rule_name:              z.string().nullable(),
  final_action:           z.string(),
  legal_hold:             z.boolean(),
  pending_destruction_id: z.string().nullable(),
});
export type EligibleDocument = z.infer<typeof EligibleDocumentSchema>;
export const EligibleDocumentListSchema = z.array(EligibleDocumentSchema);

export const DestroyedDocumentSchema = z.object({
  table_name:      z.string(),
  id:              z.string(),
  record_id:       z.string().nullable(),
  document_type:   z.string().nullable(),
  series:          z.string().nullable().optional(),
  ingested_at:     z.string().nullable().optional(),
  archived_at:     z.string().nullable().optional(),
  retention_until: z.string().nullable().optional(),
  trashed_at:      z.string().nullable().optional(),
  rule:            z.string().nullable().optional(),
  original_hash:   z.string().nullable().optional(),
  versions:        z
    .array(z.object({ version_no: z.number(), file_hash: z.string().nullable() }).passthrough())
    .optional(),
});
export type DestroyedDocument = z.infer<typeof DestroyedDocumentSchema>;

export const DESTRUCTION_STATUSES = ["proposed", "approved", "rejected", "cancelled"] as const;
export type DestructionStatus = (typeof DESTRUCTION_STATUSES)[number];

export const DestructionRecordSchema = z.object({
  id:             z.string(),
  number:         z.string(),
  kind:           z.enum(["retention", "trash_purge"]),
  status:         z.enum(DESTRUCTION_STATUSES),
  comment:        z.string().nullable(),
  document_count: z.number(),
  proposed_by:    z.string().nullable(),
  proposed_by_id: z.string().nullable(),
  proposed_at:    z.string().nullable(),
  decided_by:     z.string().nullable(),
  decided_at:     z.string().nullable(),
  decision_note:  z.string().nullable(),
  executed_at:    z.string().nullable(),
  has_pv:         z.boolean(),
  documents:      z.array(DestroyedDocumentSchema).optional(),
});
export type DestructionRecord = z.infer<typeof DestructionRecordSchema>;
export const DestructionRecordListSchema = z.array(DestructionRecordSchema);

export interface LegalHoldBody {
  reason: string;
  document?: { table_name: string; id: string };
  series_id?: string;
  search?: Record<string, unknown>;
}
