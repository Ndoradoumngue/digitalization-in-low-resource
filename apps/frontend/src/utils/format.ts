/** "reference_number" -> "Reference Number" */
export function labelFor(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Compact "Key: value · Key: value" summary of a document's extra_fields
 * blob, for a table row where there's no room for the full field list
 * (see DocumentDetailPage for the full, per-field view).
 */
function summarizeValue(v: unknown): string {
  if (Array.isArray(v)) {
    // An array of objects (e.g. a lexicon's "entries": [{kabalay, french}]) —
    // v.join(", ") would call each object's toString(), producing the
    // literal string "[object Object]" repeated. Show a count instead; the
    // full structured content is available on the document detail page.
    if (v.some((item) => item !== null && typeof item === "object")) {
      return `${v.length} ${v.length === 1 ? "entry" : "entries"}`;
    }
    return v.map(String).join(", ");
  }
  return String(v);
}

export function summarizeExtra(extra: Record<string, unknown>, max = 3): string {
  const entries = Object.entries(extra).filter(
    ([, v]) => v !== null && v !== undefined && v !== ""
              && !(Array.isArray(v) && v.length === 0)
  );
  if (entries.length === 0) return "—";
  return entries
    .slice(0, max)
    .map(([k, v]) => `${labelFor(k)}: ${summarizeValue(v)}`)
    .join(" · ");
}
