import { z } from "zod";

// ── Local/cloud sync and backups (GED Phase 8) ───────────────────────────────

export const SYNC_ALERT_KINDS = [
  "sync_down",
  "backup_failed",
  "backup_not_configured",
  "backup_stale",
  "files_failed",
  "conflicts_open",
] as const;
export type SyncAlertKind = (typeof SYNC_ALERT_KINDS)[number];

export const SyncAlertSchema = z.object({
  kind:  z.string(),
  since: z.string().nullable().optional(),
  hours: z.number().optional(),
  count: z.number().optional(),
});
export type SyncAlert = z.infer<typeof SyncAlertSchema>;

export const SyncPeerSchema = z.object({
  peer_node:        z.string(),
  received_id:      z.number(),
  sent_id:          z.number(),
  pending_changes:  z.number(),
  last_received_at: z.string().nullable(),
  last_sent_at:     z.string().nullable(),
  last_contact_at:  z.string().nullable(),
  last_success_at:  z.string().nullable(),
  last_summary:     z.record(z.string(), z.unknown()).nullable(),
});
export type SyncPeer = z.infer<typeof SyncPeerSchema>;

export const BackupRunSchema = z.object({
  target:      z.string().nullable(),
  status:      z.enum(["success", "failed", "not_configured"]),
  started_at:  z.string().nullable(),
  finished_at: z.string().nullable(),
  snapshot_id: z.string().nullable(),
  bytes_added: z.number().nullable(),
  message:     z.string().nullable(),
});
export type BackupRun = z.infer<typeof BackupRunSchema>;

export const SyncStatusSchema = z.object({
  node: z.object({
    node_id:      z.string(),
    node_role:    z.enum(["local", "cloud"]),
    sync_enabled: z.boolean(),
    // true on the node that drives sync (has a peer URL - the local one)
    initiator:    z.boolean(),
    peer_url:     z.string().nullable(),
    join_pending: z.boolean(),
  }),
  worker: z.object({
    last_cycle_at:   z.string().nullable(),
    last_success_at: z.string().nullable(),
    last_error_at:   z.string().nullable(),
    last_error:      z.string().nullable(),
    failures:        z.number(),
  }),
  changelog: z.object({
    head:      z.number(),
    epoch:     z.string(),
    seeded_at: z.string().nullable(),
  }),
  peers: z.array(SyncPeerSchema),
  files: z.object({
    pending:       z.number(),
    waiting:       z.number(),
    failed:        z.number(),
    done:          z.number(),
    pending_bytes: z.number(),
  }),
  conflicts_open:      z.number(),
  backups:             z.array(BackupRunSchema),
  last_backup_success: z.string().nullable(),
  alerts:              z.array(SyncAlertSchema),
});
export type SyncStatus = z.infer<typeof SyncStatusSchema>;

export const SyncConflictSchema = z.object({
  id:           z.string(),
  // 'field' = same field changed on both nodes; anything else = a change that could not be applied
  kind:         z.string(),
  status:       z.enum(["open", "resolved"]),
  tenant_id:    z.string().nullable(),
  table_name:   z.string().nullable(),
  pk:           z.record(z.string(), z.unknown()).nullable(),
  document_id:  z.string().nullable(),
  field:        z.string().nullable(),
  local_value:  z.unknown(),
  remote_value: z.unknown(),
  local_hlc:    z.string().nullable(),
  remote_hlc:   z.string().nullable(),
  remote_node:  z.string().nullable(),
  winner:       z.enum(["local", "remote"]).nullable(),
  message:      z.string().nullable(),
  occurrences:  z.number(),
  resolution:   z.string().nullable(),
  resolved_by:  z.string().nullable(),
  resolved_at:  z.string().nullable(),
  created_at:   z.string().nullable(),
  updated_at:   z.string().nullable(),
});
export type SyncConflict = z.infer<typeof SyncConflictSchema>;

export const SyncConflictsResponseSchema = z.object({
  total: z.number(),
  items: z.array(SyncConflictSchema),
});
export type SyncConflictsResponse = z.infer<typeof SyncConflictsResponseSchema>;

export const SYNC_RESOLUTIONS = ["keep_local", "keep_remote", "manual", "retry", "dismiss"] as const;
export type SyncResolution = (typeof SYNC_RESOLUTIONS)[number];

export const SyncResolveResponseSchema = z.object({
  id:         z.string(),
  status:     z.literal("resolved"),
  resolution: z.enum(SYNC_RESOLUTIONS),
  written:    z.boolean(),
});
export type SyncResolveResponse = z.infer<typeof SyncResolveResponseSchema>;

export const SyncFileSchema = z.object({
  path:            z.string(),
  direction:       z.enum(["push", "fetch"]),
  status:          z.enum(["pending", "waiting", "done", "failed"]),
  table_name:      z.string().nullable(),
  attempts:        z.number(),
  last_error:      z.string().nullable(),
  next_attempt_at: z.string().nullable(),
  updated_at:      z.string().nullable(),
});
export type SyncFile = z.infer<typeof SyncFileSchema>;

export const SyncFilesResponseSchema = z.object({ items: z.array(SyncFileSchema) });
export type SyncFilesResponse = z.infer<typeof SyncFilesResponseSchema>;
