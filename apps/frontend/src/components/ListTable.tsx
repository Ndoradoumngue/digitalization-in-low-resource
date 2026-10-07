import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { labelFor } from "../utils/format";

/** An object, or an array holding objects (e.g. a lexicon's "entries":
 *  [{kabalay, french}]) - shown as a table rather than plain text. */
export function isComplexValue(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (Array.isArray(v)) return v.some((item) => item !== null && typeof item === "object");
  return typeof v === "object";
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Read-only view of a list-of-objects field: a searchable table with one
 *  column per key, a row count, scrolling inside a fixed height. `toolbar`
 *  is rendered at the end of the search row (e.g. an edit button). */
export default function ListTable({ value, toolbar }: { value: unknown; toolbar?: React.ReactNode }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const rows = useMemo(
    () => (Array.isArray(value) ? value : [value]).filter((r) => r !== null && r !== undefined),
    [value],
  );
  const columns = useMemo(() => {
    const cols: string[] = [];
    for (const r of rows) {
      if (r && typeof r === "object" && !Array.isArray(r)) {
        for (const k of Object.keys(r as Record<string, unknown>)) if (!cols.includes(k)) cols.push(k);
      }
    }
    return cols;
  }, [rows]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const indexed = rows.map((r, i) => [i, r] as const);
    if (!q) return indexed;
    return indexed.filter(([, r]) => cellText(r).toLowerCase().includes(q));
  }, [rows, query]);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("listTable.search")}
          className="flex-1 rounded-md border border-gray-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
        />
        <span className="text-[11px] text-gray-400 whitespace-nowrap">
          {query
            ? t("listTable.filtered", { shown: filtered.length, count: rows.length })
            : t("listTable.count", { count: rows.length })}
        </span>
        {toolbar}
      </div>
      {columns.length > 0 ? (
        <div className="max-h-80 overflow-auto rounded-md border border-gray-200">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-50">
              <tr>
                <th className="px-2 py-1 text-left font-semibold text-gray-400 w-10">#</th>
                {columns.map((c) => (
                  <th key={c} className="px-2 py-1 text-left font-semibold text-gray-500">{labelFor(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(([i, r]) => (
                <tr key={i} className="border-t border-gray-100 align-top">
                  <td className="px-2 py-1 text-gray-300">{i + 1}</td>
                  {columns.map((c) => (
                    <td key={c} className="px-2 py-1 text-gray-800 break-words">
                      {cellText((r as Record<string, unknown>)[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <pre className="max-h-80 overflow-auto rounded-md bg-gray-50 p-2 text-xs">{JSON.stringify(value, null, 2)}</pre>
      )}
    </div>
  );
}
