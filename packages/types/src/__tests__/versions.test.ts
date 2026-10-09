import { describe, expect, it } from "vitest";
import {
  AuditChainResultSchema,
  DocumentHistoryResponseSchema,
  DocumentVersionsResponseSchema,
  IntegrityCheckResultSchema,
  LastIntegrityRunSchema,
  VersionChangeResponseSchema,
} from "../index";

const version = {
  version_no: 2,
  file_hash: "ab".repeat(32),
  size: 1234,
  mime: "image/png",
  original_filename: "scan v2.png",
  comment: null,
  restored_from: null,
  created_by: "u1",
  created_by_email: "editor@example.com",
  created_at: "2026-10-09T10:00:00+00:00",
  is_current: true,
};

describe("version and history schemas (GED Phase 2)", () => {
  it("parses a version list, including a backfilled v1 with no hash or author", () => {
    const parsed = DocumentVersionsResponseSchema.parse({
      current_version: 2,
      versions: [
        version,
        { ...version, version_no: 1, file_hash: null, size: null, created_by: null, created_by_email: null, is_current: false },
      ],
    });
    expect(parsed.versions[1].file_hash).toBeNull();
  });

  it("parses an upload/restore answer with and without a re-extraction batch", () => {
    expect(VersionChangeResponseSchema.parse({ version, batch_id: "b1" }).batch_id).toBe("b1");
    expect(VersionChangeResponseSchema.parse({ version: { ...version, restored_from: 1 }, batch_id: null }).version.restored_from).toBe(1);
  });

  it("parses field changes with any JSON value and rejects an unknown source", () => {
    const change = {
      id: "h1",
      field: "items",
      old_value: ["a"],
      new_value: null,
      change_source: "reextract",
      version_no: 2,
      changed_by: null,
      changed_by_email: null,
      created_at: "2026-10-09T10:00:00+00:00",
    };
    const parsed = DocumentHistoryResponseSchema.parse({ fields: [change], events: [] });
    expect(parsed.fields[0].old_value).toEqual(["a"]);
    expect(() => DocumentHistoryResponseSchema.parse({ fields: [{ ...change, change_source: "magic" }], events: [] })).toThrow();
  });
});

describe("integrity schemas (GED Phase 2)", () => {
  it("accepts the old fixity-only answer and the new one with versions and chain", () => {
    expect(IntegrityCheckResultSchema.parse({ checked: 1, ok: 1, mismatched: 0, missing: 0 }).audit_chain).toBeUndefined();
    const parsed = IntegrityCheckResultSchema.parse({
      checked: 1, ok: 1, mismatched: 0, missing: 0, versions_checked: 2, versions_ok: 2,
      audit_chain: { ok: false, checked: 9, first_broken: { reason: "row_altered", origin_node: "local", chain_seq: 3, id: "x" } },
    });
    expect(parsed.audit_chain?.first_broken?.reason).toBe("row_altered");
  });

  it("parses the chain verification and the last run (or none)", () => {
    const chain = AuditChainResultSchema.parse({
      ok: false, checked: 0, chains: [], first_broken: { reason: "unchained", origin_node: null, chain_seq: null, id: null, count: 2 },
    });
    expect(chain.first_broken?.count).toBe(2);
    expect(LastIntegrityRunSchema.parse({ last_run: null }).last_run).toBeNull();
    const last = LastIntegrityRunSchema.parse({
      last_run: { ran_at: "2026-10-09T10:00:00+00:00", ran_by: null, fixity: { checked: 0, ok: 0, mismatched: 0, missing: 0 }, audit_chain: null },
    });
    expect(last.last_run?.ran_by).toBeNull();
  });
});
