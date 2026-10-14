import { describe, expect, it } from "vitest";
import { DbDocumentListSchema } from "../documents_db";

const base = {
  id: "d1",
  table_name: "t_default_decree",
  document_type: "decree",
  record_id: null,
  confidence: "high",
  review_status: "approved",
  ingested_at: null,
  source_image_path: null,
  reviewed_by: null,
  series_id: null,
  extra_fields: { subject: "Décret" },
};

describe("document list search details", () => {
  it("parses snippets and matching pages", () => {
    const list = DbDocumentListSchema.parse({
      total: 1,
      page: 1,
      page_size: 20,
      results: [
        {
          ...base,
          search: {
            snippet: [],
            pages: [
              { page: 2, snippet: [{ text: "Objet : ", hit: false }, { text: "ordonnance", hit: true }] },
              { page: 7, snippet: null },
            ],
          },
        },
      ],
    });
    const search = list.results[0].search!;
    expect(search.pages.map((p) => p.page)).toEqual([2, 7]);
    expect(search.pages[0].snippet![1]).toEqual({ text: "ordonnance", hit: true });
  });

  it("leaves search out of a plain listing", () => {
    const list = DbDocumentListSchema.parse({ total: 1, page: 1, page_size: 20, results: [base] });
    expect(list.results[0].search).toBeUndefined();
  });

  it("rejects a malformed page hit", () => {
    expect(() =>
      DbDocumentListSchema.parse({
        total: 1, page: 1, page_size: 20,
        results: [{ ...base, search: { snippet: [], pages: [{ page: "2" }] } }],
      }),
    ).toThrow();
  });
});
