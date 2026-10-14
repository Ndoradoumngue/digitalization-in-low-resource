// Display helpers for the lifecycle views (GED Phase 6).

/** A date ("2026-10-14") or timestamp as a localized date. Plain dates are
 * read as UTC so they never shift by a day in the browser's timezone. */
export function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale, iso.length === 10 ? { timeZone: "UTC" } : undefined);
}

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  return iso ? new Date(iso).toLocaleString(locale) : "";
}
