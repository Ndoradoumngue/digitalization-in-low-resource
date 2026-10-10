import { describe, expect, it } from "vitest";
import {
  buildSeriesTree,
  LinkSuggestionListSchema,
  SeriesListResponseSchema,
  seriesTrail,
  subtreeIds,
  TagListSchema,
} from "../index";

const series = SeriesListResponseSchema.parse([
  { id: "a", name: "Administration", description: null, created_at: null, parent_id: null, code: "1", path: "/a/", position: 0, system_key: null, document_count: 2 },
  { id: "b", name: "Personnel", description: null, created_at: null, parent_id: "a", code: "1.1", path: "/a/b/", position: 0, system_key: null, document_count: 1 },
  { id: "c", name: "Contrats", description: null, created_at: null, parent_id: "b", code: null, path: "/a/b/c/", position: 0, system_key: null },
  { id: "f", name: "À classer", description: null, created_at: null, parent_id: null, code: null, path: "/f/", position: 9999, system_key: "to_be_filed" },
  { id: "o", name: "Orphan", description: null, created_at: null, parent_id: "not-synced", code: null, path: "/x/o/", position: 0, system_key: null },
]);

describe("series tree helpers", () => {
  it("nests children and keeps orphans at the root", () => {
    const tree = buildSeriesTree(series);
    expect(tree.map((n) => n.id)).toEqual(["a", "f", "o"]);
    expect(tree[0].children[0].children[0].id).toBe("c");
  });

  it("gives the breadcrumb from the root", () => {
    expect(seriesTrail(series, "c").map((s) => s.name)).toEqual(["Administration", "Personnel", "Contrats"]);
    expect(seriesTrail(series, null)).toEqual([]);
  });

  it("lists a subtree by path", () => {
    expect([...subtreeIds(series, "a")].sort()).toEqual(["a", "b", "c"]);
    expect([...subtreeIds(series, "c")]).toEqual(["c"]);
  });

  it("still parses the pre-hierarchy series shape", () => {
    expect(SeriesListResponseSchema.parse([{ id: "x", name: "X", description: null, created_at: null }])[0].parent_id).toBeUndefined();
  });

  it("parses tags and link suggestions", () => {
    expect(TagListSchema.parse([{ id: "t", name: "urgent", color: null, document_count: 3 }])[0].name).toBe("urgent");
    const s = LinkSuggestionListSchema.parse([
      { table_name: "t_default_deed", id: "d", record_id: "LAND-2026-000001", document_type: "deed", label: "N°1", matched: "N°1", field: "reference_number", direction: "mentions" },
    ]);
    expect(s[0].direction).toBe("mentions");
  });
});
