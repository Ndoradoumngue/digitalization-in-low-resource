import { describe, expect, it } from "vitest";
import {
  CommentListSchema,
  DocumentLockSchema,
  NotificationListSchema,
  NotificationPreferencesSchema,
  SignatureRequestSchema,
  SignatureVerificationSchema,
  SigningStatusSchema,
} from "../collaboration";

// Payload shapes of apps/api/notifications_router.py, checkout_router.py,
// comments_router.py and signature_router.py (GED Phase 7).

describe("collaboration schemas", () => {
  it("parses notifications, including a stripped one and an unknown kind", () => {
    const list = NotificationListSchema.parse([
      {
        id: "n1",
        kind: "mention",
        table_name: "t_default_invoice",
        document_id: "d1",
        payload: { record_id: "INV-1", by: "Admin", comment_id: "c1" },
        link: "/documents/t_default_invoice/d1",
        created_at: "2026-10-14T10:00:00+00:00",
        read_at: null,
      },
      {
        id: "n2",
        kind: "document_shared",
        table_name: null,
        document_id: null,
        payload: { unavailable: true },
        link: "/notifications",
        created_at: "2026-10-14T10:00:00+00:00",
        read_at: "2026-10-14T11:00:00+00:00",
      },
      {
        id: "n3",
        kind: "some_future_kind",
        table_name: null,
        document_id: null,
        payload: {},
        link: "/notifications",
        created_at: null,
        read_at: null,
      },
    ]);
    expect(list).toHaveLength(3);
    expect(NotificationPreferencesSchema.parse({ email_notifications: "digest", locale: null, email_enabled: false }).email_notifications).toBe("digest");
    expect(() => NotificationPreferencesSchema.parse({ email_notifications: "weekly", locale: null, email_enabled: true })).toThrow();
  });

  it("parses a lock", () => {
    const lock = DocumentLockSchema.parse({
      locked: true,
      locked_by: "u1",
      locked_by_name: "Editor",
      locked_until: "2026-10-14T18:00:00+00:00",
      mine: false,
      can_checkout: false,
      can_checkin: false,
      can_force_unlock: true,
      default_hours: 8,
      max_hours: 72,
    });
    expect(lock.locked_by_name).toBe("Editor");
  });

  it("parses comments, deleted ones without a body", () => {
    const comments = CommentListSchema.parse([
      {
        id: "c1", parent_id: null, version_no: 1, author_id: "u1", author_name: "Rev",
        body: "@Admin regardez", mentions: [{ id: "u2", name: "Admin" }],
        created_at: "2026-10-14T10:00:00+00:00", edited_at: null, deleted: false, can_edit: true, can_delete: true,
      },
      {
        id: "c2", parent_id: "c1", version_no: 1, author_id: "u2", author_name: "Admin",
        body: null, mentions: [], created_at: "2026-10-14T10:05:00+00:00", edited_at: null,
        deleted: true, can_edit: false, can_delete: false,
      },
    ]);
    expect(comments[1].body).toBeNull();
  });

  it("parses signature requests, status and verification", () => {
    const req = SignatureRequestSchema.parse({
      id: "r1", table_name: "t_default_invoice", document_id: "d1", requested_by: "u1",
      requested_by_name: "Editor", reason: "Approbation", message: null, status: "pending",
      created_at: "2026-10-14T10:00:00+00:00", closed_at: null, close_note: null,
      signers: [
        { user_id: "u2", order: 1, title: "Chef", status: "signed", signed_at: "2026-10-14T10:10:00+00:00", signature_id: "s1", name: "Rev", is_next: false },
        { user_id: "u3", order: 2, title: null, status: "pending", signed_at: null, signature_id: null, name: "Rev2", is_next: true },
      ],
      can_sign: false, can_cancel: true,
    });
    expect(req.signers[1].is_next).toBe(true);
    expect(SigningStatusSchema.parse({ configured: false, reason: null }).configured).toBe(false);
    const v = SignatureVerificationSchema.parse({
      level: "simple",
      evidence: [{
        id: "s1", request_id: "r1", version_no: 2, source_version_no: 1, field_name: "SDAI_Signature_1",
        signer_id: "u2", signer_name: "Rev", signer_title: "Chef", reason: "Approbation", level: "simple",
        signed_at: "2026-10-14T10:10:00+00:00", ip_address: "10.0.0.1", cert_subject: "Common Name: Org",
        cert_fingerprint: "ab", source_file_hash: "aa", signed_file_hash: "bb",
      }],
      verification: {
        version_no: 2, file_hash: "bb", file_hash_ok: true, all_ok: true,
        signatures: [{
          field: "SDAI_Signature_1", signer_name: "Rev", reason: "Approbation", intact: true, valid: true,
          trusted: true, coverage: "ENTIRE_FILE", modification_level: "NONE", signed_at: "2026-10-14T10:10:00+00:00",
          cert_subject: "Common Name: Org", cert_fingerprint: "ab", summary: "INTACT:TRUSTED,UNTOUCHED",
          ok: true, evidence_id: "s1", evidence_matches: true,
        }],
      },
      audit: [{ action: "document_signed", user_email: "rev@example.com", created_at: "2026-10-14T10:10:00+00:00", details: {} }],
    });
    expect(v.verification?.all_ok).toBe(true);
    expect(SignatureVerificationSchema.parse({ level: "simple", evidence: [], verification: null, audit: [] }).verification).toBeNull();
  });
});
