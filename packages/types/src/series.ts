import { z } from "zod";

// ── Plan de classement (series tree, GED Phase 3) ────────────────────────────

export const SeriesSchema = z.object({
  id:             z.string(),
  name:           z.string(),
  description:    z.string().nullable(),
  created_at:     z.string().nullable(),
  // null for a root series
  parent_id:      z.string().nullable().optional(),
  // archival classification code shown to users, e.g. "1.2.3"
  code:           z.string().nullable().optional(),
  // ids from the root down to the series itself: "/<root>/.../<self>/"
  path:           z.string().nullable().optional(),
  position:       z.number().optional(),
  // "to_be_filed" = where documents with no filing rule land (shown translated)
  system_key:     z.string().nullable().optional(),
  // documents of this series the caller can see (not counting sub-series)
  document_count: z.number().optional(),
});
export type Series = z.infer<typeof SeriesSchema>;

export const SeriesListResponseSchema = z.array(SeriesSchema);

export const CreateSeriesResponseSchema = z.object({
  id:        z.string(),
  name:      z.string(),
  parent_id: z.string().nullable().optional(),
  path:      z.string().optional(),
});

export interface SeriesNode extends Series {
  children: SeriesNode[];
}

/** Nest a flat, ordered series list; a series whose parent is unknown
 * (not synchronised yet) shows at the root rather than disappearing. */
export function buildSeriesTree(series: Series[]): SeriesNode[] {
  const byId = new Map<string, SeriesNode>(series.map((s) => [s.id, { ...s, children: [] }]));
  const roots: SeriesNode[] = [];
  for (const s of series) {
    const node = byId.get(s.id)!;
    const parent = s.parent_id ? byId.get(s.parent_id) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  return roots;
}

/** Ancestors of a series, root first, the series itself last. */
export function seriesTrail(series: Series[], id: string | null | undefined): Series[] {
  if (!id) return [];
  const byId = new Map(series.map((s) => [s.id, s]));
  const trail: Series[] = [];
  const seen = new Set<string>();
  let current = byId.get(id);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    trail.unshift(current);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return trail;
}

/** The series and every series below it. */
export function subtreeIds(series: Series[], id: string): Set<string> {
  return new Set(series.filter((s) => s.id === id || (s.path ?? "").includes(`/${id}/`)).map((s) => s.id));
}

// ── Tags ─────────────────────────────────────────────────────────────────────

export const TagSchema = z.object({
  id:             z.string(),
  name:           z.string(),
  color:          z.string().nullable(),
  // documents carrying it that the caller can see (vocabulary listing only)
  document_count: z.number().optional(),
});
export type Tag = z.infer<typeof TagSchema>;
export const TagListSchema = z.array(TagSchema);

// ── Automatic filing ─────────────────────────────────────────────────────────

export const FilingRulesSchema = z.object({
  rules: z.array(z.object({ table_name: z.string(), series_id: z.string().nullable() })),
  to_be_filed_series_id: z.string().nullable(),
});
export type FilingRules = z.infer<typeof FilingRulesSchema>;

// ── Link suggestions ─────────────────────────────────────────────────────────

export const LinkSuggestionSchema = z.object({
  table_name:    z.string(),
  id:            z.string(),
  record_id:     z.string().nullable(),
  document_type: z.string().nullable(),
  label:         z.string().nullable(),
  // the reference value found
  matched:       z.string().nullable(),
  // their field holding it (direction "mentions")
  field:         z.string().nullable(),
  // "mentions": this document cites theirs - "mentioned_by": theirs cites this one
  direction:     z.enum(["mentions", "mentioned_by"]),
});
export type LinkSuggestion = z.infer<typeof LinkSuggestionSchema>;
export const LinkSuggestionListSchema = z.array(LinkSuggestionSchema);
