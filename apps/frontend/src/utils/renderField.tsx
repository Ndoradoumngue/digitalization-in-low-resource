import { labelFor } from "./format";

// Extracted fields are arbitrary per tenant/schema — some are scalars, some
// are arrays of scalars (e.g. person_names), and some are arrays of objects
// (e.g. a lexicon's "entries": [{kabalay, french}, ...] or a table of
// contents: [{section, pages}, ...]). String(value) on the latter produces
// "[object Object],[object Object],..." (Array.prototype.toString calls
// each element's own toString, and a plain object's is always that literal
// string) — this renders each shape properly instead.

export function renderObjectInline(obj: Record<string, unknown>): string {
  return Object.entries(obj)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${labelFor(k)}: ${Array.isArray(v) ? v.map(String).join(", ") : String(v)}`)
    .join(" · ");
}

export function renderFieldValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === "") {
    return <span className="text-gray-400 italic">—</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-400 italic">—</span>;
    const hasObjects = value.some((v) => v !== null && typeof v === "object");
    if (!hasObjects) return value.map(String).join(", ");
    return (
      <ul className="list-disc list-inside space-y-0.5">
        {value.map((item, i) => (
          <li key={i}>
            {item !== null && typeof item === "object"
              ? renderObjectInline(item as Record<string, unknown>)
              : String(item)}
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    return renderObjectInline(value as Record<string, unknown>);
  }
  return String(value);
}
