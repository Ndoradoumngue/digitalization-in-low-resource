import { describe, expect, it } from "vitest";
import {
  DestructionRecordSchema,
  DocumentLifecycleSchema,
  EligibleDocumentSchema,
  LegalHoldSchema,
  RetentionRuleSchema,
  TrashPageSchema,
} from "../lifecycle";
import { DocumentLinkSchema } from "../links";

// Payloads captured from the real API (apps/api/lifecycle_router.py) against
// the test database, trimmed to one item per list.

const lifecycle = {
  "lifecycle_status": "active",
  "archived_at": null,
  "retention_until": null,
  "retention_override": null,
  "retention_rule": null,
  "legal_hold": true,
  "holds": [
    {
      "id": "d34da604-142a-499b-a956-42a888f219f8",
      "scope": "documents",
      "origin": "document",
      "reason": "Demande du juge",
      "created_at": "2026-10-14T10:00:35.938339+00:00",
      "created_by": "archivist@example.com",
      "series_name": null
    }
  ],
  "in_trash": false,
  "deleted_at": null,
  "deleted_reason": null,
  "purge_at": null,
  "actions": [
    "archive",
    "hold",
    "release_hold"
  ]
};

const trash = {
  "items": [
    {
      "table_name": "t_default_invoice",
      "id": "1220fef3-baa6-4904-ae01-15e80134f9c0",
      "record_id": "INV-2026-000035",
      "document_type": "invoice",
      "display": "REF-35",
      "lifecycle_status": "active",
      "deleted_at": "2026-10-14T10:00:39.030772+00:00",
      "deleted_by": "archivist@example.com",
      "deleted_reason": null,
      "purge_at": "2026-11-13T10:00:39.030772+00:00",
      "restorable": true,
      "legal_hold": false
    }
  ],
  "total": 6,
  "page": 1,
  "page_size": 50,
  "retention_days": 30
};

const rule = {
  "id": "fc7f6e54-a7f3-40cb-b816-17db9f9483d2",
  "name": "Actes",
  "scope": "series",
  "series_id": "a1598703-93da-4636-99e3-88544a8ed0cb",
  "table_name": null,
  "duration_months": 120,
  "trigger_on": "document_date",
  "date_field": "date_acte",
  "final_action": "destroy",
  "created_at": "2026-10-14T10:00:33.568679+00:00",
  "series_name": "Actes",
  "series_code": null,
  "document_count": 2
};

const hold = {
  "id": "a83e9d6a-351b-4e0b-8c79-809145c9756a",
  "scope": "documents",
  "origin": "document",
  "series_id": null,
  "series_name": null,
  "query": null,
  "reason": "Audit",
  "created_at": "2026-10-14T10:00:38.124290+00:00",
  "created_by": "archivist@example.com",
  "released_at": null,
  "released_by": null,
  "release_reason": null,
  "document_count": 1
};

const eligible = {
  "table_name": "t_default_invoice",
  "id": "dc61f7e3-d09b-4730-94ef-0f2574af85ad",
  "record_id": "INV-2026-000002",
  "document_type": "invoice",
  "display": "REF-2",
  "retention_until": null,
  "archived_at": null,
  "rule_name": null,
  "final_action": "review",
  "legal_hold": false,
  "pending_destruction_id": null
};

const record = {
  "id": "24788b67-ad8e-4c3c-be58-2b4cbfb9a69a",
  "number": "ELIM-2026-24788B67",
  "kind": "retention",
  "status": "approved",
  "comment": null,
  "document_count": 1,
  "proposed_by": "archivist@example.com",
  "proposed_by_id": "7621737b-8f88-4c7c-8df7-b47df5e8be2b",
  "proposed_at": "2026-10-14T10:00:37.531147+00:00",
  "decided_by": "admin@example.com",
  "decided_at": "2026-10-14T10:00:37.615687+00:00",
  "decision_note": null,
  "executed_at": "2026-10-14T10:00:37.615687+00:00",
  "has_pv": true,
  "documents": [
    {
      "id": "48c6d29f-d2e7-40bd-ac83-da0b9583f7a9",
      "rule": null,
      "series": null,
      "versions": [
        {
          "size": 54,
          "filename": "original.pdf",
          "file_hash": "76eeef31ad35c08ac2b7cfab027f35856c4edc7c1f5e52fbf330ff60a588dd3d",
          "created_at": "2026-10-14T10:00:37.465013+00:00",
          "version_no": 1
        }
      ],
      "record_id": "INV-2026-000029",
      "table_name": "t_default_invoice",
      "trashed_at": null,
      "archived_at": null,
      "ingested_at": "2026-10-14T10:00:37.465013+00:00",
      "content_hash": "76eeef31ad35c08ac2b7cfab027f35856c4edc7c1f5e52fbf330ff60a588dd3d",
      "document_type": "invoice",
      "original_hash": "76eeef31ad35c08ac2b7cfab027f35856c4edc7c1f5e52fbf330ff60a588dd3d",
      "confidentiality": "internal",
      "retention_until": null
    }
  ]
};

describe("lifecycle (GED Phase 6)", () => {
  it("parses a document's lifecycle with its holds and allowed actions", () => {
    const res = DocumentLifecycleSchema.parse(lifecycle);
    expect(res.legal_hold).toBe(true);
    expect(res.holds[0].origin).toBe("document");
    expect(res.actions).toContain("release_hold");
  });

  it("rejects an unknown lifecycle status or action", () => {
    expect(() => DocumentLifecycleSchema.parse({ ...lifecycle, lifecycle_status: "deleted" })).toThrow();
    expect(() => DocumentLifecycleSchema.parse({ ...lifecycle, actions: ["purge"] })).toThrow();
  });

  it("parses the trash page", () => {
    const res = TrashPageSchema.parse(trash);
    expect(res.retention_days).toBe(30);
    expect(res.items[0].restorable).toBe(true);
  });

  it("parses retention rules and refuses unknown triggers / outcomes", () => {
    expect(RetentionRuleSchema.parse(rule).date_field).toBe("date_acte");
    expect(() => RetentionRuleSchema.parse({ ...rule, trigger_on: "signed_at" })).toThrow();
    expect(() => RetentionRuleSchema.parse({ ...rule, final_action: "shred" })).toThrow();
  });

  it("parses legal holds", () => {
    expect(LegalHoldSchema.parse(hold).document_count).toBe(1);
    expect(LegalHoldSchema.parse({ ...hold, origin: "series", scope: "series", series_name: "Contentieux" }).scope).toBe("series");
  });

  it("parses documents to destroy", () => {
    expect(EligibleDocumentSchema.parse(eligible).final_action).toBe("review");
  });

  it("parses a destruction record with the frozen document list", () => {
    const res = DestructionRecordSchema.parse(record);
    expect(res.number).toMatch(/^ELIM-\d{4}-/);
    expect(res.documents?.[0].versions?.[0].file_hash).toHaveLength(64);
    expect(() => DestructionRecordSchema.parse({ ...record, status: "executed" })).toThrow();
  });

  it("keeps links to destroyed documents as tombstones", () => {
    const link = DocumentLinkSchema.parse({
      id: "l1",
      relation: "concerne",
      note: null,
      created_at: null,
      created_by: null,
      direction: "outgoing",
      other_table: "t_default_invoice",
      other_id: "d1",
      other_document_type: "invoice",
      other_display: null,
      other_source_image_path: null,
      other_missing: false,
      other_destroyed: true,
      other_record_id: "INV-2026-000029",
    });
    expect(link.other_destroyed).toBe(true);
  });
});
