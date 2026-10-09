import { describe, expect, it } from "vitest";
import {
  ReviewQueueItemSchema,
  SyncConflictsResponseSchema,
  SyncResolveResponseSchema,
  SyncStatusSchema,
} from "../index";

const status = {
  node: {
    node_id: "local-ndj-01",
    node_role: "local",
    sync_enabled: true,
    initiator: true,
    peer_url: "https://ged.example.td",
    join_pending: false,
  },
  worker: {
    last_cycle_at: "2026-10-09T10:00:00+00:00",
    last_success_at: "2026-10-09T10:00:00+00:00",
    last_error_at: null,
    last_error: null,
    failures: 0,
  },
  changelog: { head: 1234, epoch: "6f1c8a2e-0000-4000-8000-000000000001", seeded_at: null },
  peers: [
    {
      peer_node: "cloud-do-01",
      received_id: 40,
      sent_id: 1200,
      pending_changes: 34,
      last_received_at: "2026-10-09T10:00:00+00:00",
      last_sent_at: "2026-10-09T10:00:00+00:00",
      last_contact_at: "2026-10-09T10:00:00+00:00",
      last_success_at: null,
      last_summary: { pushed: 3, pulled: 1 },
    },
  ],
  files: { pending: 2, waiting: 1, failed: 0, done: 50, pending_bytes: 1048576 },
  conflicts_open: 1,
  backups: [
    {
      target: "sftp",
      status: "failed",
      started_at: "2026-10-09T00:30:00+00:00",
      finished_at: "2026-10-09T00:31:00+00:00",
      snapshot_id: null,
      bytes_added: null,
      message: "RESTIC_PASSWORD does not open this repository",
    },
  ],
  last_backup_success: null,
  alerts: [{ kind: "backup_failed", since: null }, { kind: "conflicts_open", count: 1 }],
};

describe("sync schemas", () => {
  it("parses the admin status payload", () => {
    const parsed = SyncStatusSchema.parse(status);
    expect(parsed.peers[0].pending_changes).toBe(34);
    expect(parsed.alerts.map((a) => a.kind)).toEqual(["backup_failed", "conflicts_open"]);
  });

  it("rejects an unknown backup status", () => {
    const bad = { ...status, backups: [{ ...status.backups[0], status: "maybe" }] };
    expect(() => SyncStatusSchema.parse(bad)).toThrow();
  });

  it("parses field conflicts and failed changes", () => {
    const parsed = SyncConflictsResponseSchema.parse({
      total: 2,
      items: [
        {
          id: "c1", kind: "field", status: "open", tenant_id: "t", table_name: "t_default_invoice",
          pk: { id: "d" }, document_id: "d", field: "amount", local_value: "100", remote_value: ["a"],
          local_hlc: "1700000000000:00001:local", remote_hlc: "1700000000001:00001:cloud",
          remote_node: "cloud", winner: "remote", message: null, occurrences: 2, resolution: null,
          resolved_by: null, resolved_at: null, created_at: null, updated_at: null,
        },
        {
          id: "c2", kind: "unique_violation", status: "resolved", tenant_id: null, table_name: "sdai_tenants",
          pk: { id: "x" }, document_id: null, field: null, local_value: null, remote_value: null,
          local_hlc: null, remote_hlc: null, remote_node: "cloud", winner: null, message: "duplicate key",
          occurrences: 1, resolution: "dismiss", resolved_by: "u", resolved_at: null, created_at: null,
          updated_at: null,
        },
      ],
    });
    expect(parsed.items[0].remote_value).toEqual(["a"]);
    expect(SyncResolveResponseSchema.parse({ id: "c1", status: "resolved", resolution: "keep_local", written: true }).written).toBe(true);
  });

  it("review queue items may carry the sync conflict flag", () => {
    const item = ReviewQueueItemSchema.parse({
      id: "d", table_name: "t", document_type: null, record_id: null, confidence: null,
      review_status: "approved", ingested_at: null, source_image_path: null, extra_fields: {},
      sync_conflict: true,
    });
    expect(item.sync_conflict).toBe(true);
  });
});
