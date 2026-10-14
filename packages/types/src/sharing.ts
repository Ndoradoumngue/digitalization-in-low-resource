import { z } from "zod";

// ── External share links (GED Phase 5) ───────────────────────────────────────

export const SHARE_STATUSES = ["active", "expired", "revoked", "exhausted"] as const;
export type ShareStatus = (typeof SHARE_STATUSES)[number];

export const ShareLinkSchema = z.object({
  id:                 z.string(),
  // null = whatever version is current when the link is used
  version_no:         z.number().nullable(),
  expires_at:         z.string(),
  max_downloads:      z.number().nullable(),
  note:               z.string().nullable(),
  created_by:         z.string(),
  created_by_email:   z.string().nullable(),
  created_at:         z.string(),
  revoked_at:         z.string().nullable(),
  revoked_by:         z.string().nullable(),
  has_password:       z.boolean(),
  download_count:     z.number(),
  last_downloaded_at: z.string().nullable(),
  status:             z.enum(SHARE_STATUSES),
});
export type ShareLink = z.infer<typeof ShareLinkSchema>;

export const ShareListResponseSchema = z.object({ shares: z.array(ShareLinkSchema) });
export type ShareListResponse = z.infer<typeof ShareListResponseSchema>;

export const CreateShareResponseSchema = z.object({
  share: ShareLinkSchema,
  // The full link - returned only once, never stored in clear.
  url:   z.string(),
});
export type CreateShareResponse = z.infer<typeof CreateShareResponseSchema>;

export const SHARE_MAX_DAYS = 30;

// ── Selective export (GED Phase 5) ───────────────────────────────────────────

export const EXPORT_STATUSES = ["queued", "running", "done", "failed", "expired", "deleted"] as const;
export type ExportStatus = (typeof EXPORT_STATUSES)[number];

export const ExportWarningSchema = z.object({
  document:   z.string(),
  version_no: z.number().nullable(),
  problem:    z.enum(["missing", "hash_mismatch"]),
});
export type ExportWarning = z.infer<typeof ExportWarningSchema>;

export const ExportJobSchema = z.object({
  id:             z.string(),
  status:         z.enum(EXPORT_STATUSES),
  selection:      z.record(z.string(), z.unknown()).nullable(),
  versions:       z.enum(["current", "all"]),
  document_count: z.number().nullable(),
  file_count:     z.number().nullable(),
  total_bytes:    z.number().nullable(),
  zip_sha256:     z.string().nullable(),
  warnings:       z.array(ExportWarningSchema).nullable(),
  error:          z.string().nullable(),
  created_at:     z.string(),
  started_at:     z.string().nullable(),
  finished_at:    z.string().nullable(),
  expires_at:     z.string().nullable(),
});
export type ExportJob = z.infer<typeof ExportJobSchema>;

export const ExportJobsResponseSchema = z.object({ jobs: z.array(ExportJobSchema) });
export type ExportJobsResponse = z.infer<typeof ExportJobsResponseSchema>;

export interface ExportDocumentRef {
  table_name: string;
  id: string;
}

export interface ExportSearchSelection {
  q?: string;
  document_type?: string;
  confidence?: string;
  review_status?: string;
  date_from?: string;
  date_to?: string;
  series?: string;
  include_subseries?: boolean;
  tag?: string;
  reviewed_by?: string;
}

/** Exactly one of documents / series_id / search. */
export type ExportRequest = { versions?: "current" | "all" } & (
  | { documents: ExportDocumentRef[] }
  | { series_id: string; include_sub?: boolean }
  | { search: ExportSearchSelection }
);
