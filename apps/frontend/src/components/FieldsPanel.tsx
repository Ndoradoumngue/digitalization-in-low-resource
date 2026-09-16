import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updateDocumentFields } from "@sdai/api-client";
import { labelFor } from "../utils/format";
import { renderFieldValue } from "../utils/renderField";

// A field is "complex" (object, or array containing objects — e.g. a
// lexicon's "entries": [{kabalay, french}]) if a plain text input can't
// represent it. Those get a raw-JSON textarea instead; everything else
// (scalars, arrays of scalars) gets the same comma-separated text input
// the review form uses.
function isComplexValue(v: unknown): boolean {
  if (v === null || v === undefined) return false;
  if (Array.isArray(v)) return v.some((item) => item !== null && typeof item === "object");
  return typeof v === "object";
}

interface Props {
  tableName: string;
  id:        string;
  fieldRows: [string, unknown][];
  canEdit:   boolean;
  title?:    string;
}

export default function FieldsPanel({ tableName, id, fieldRows, canEdit, title = "Fields" }: Props) {
  const queryClient = useQueryClient();
  const [editing, setEditing]       = useState(false);
  const [form, setForm]             = useState<Record<string, string>>({});
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});

  const mutation = useMutation({
    mutationFn: (fields: Record<string, unknown>) => updateDocumentFields(tableName, id, fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["db-document", tableName, id] });
      setEditing(false);
    },
  });

  function startEditing() {
    const vals: Record<string, string> = {};
    for (const [k, v] of fieldRows) {
      if (isComplexValue(v)) {
        vals[k] = JSON.stringify(v, null, 2);
      } else if (Array.isArray(v)) {
        vals[k] = v.map(String).join(", ");
      } else {
        vals[k] = v == null ? "" : String(v);
      }
    }
    setForm(vals);
    setJsonErrors({});
    setEditing(true);
  }

  function save() {
    const payload: Record<string, unknown> = {};
    const errors: Record<string, string> = {};
    for (const [k, v] of Object.entries(form)) {
      const original = fieldRows.find(([key]) => key === k)?.[1];
      if (isComplexValue(original)) {
        try {
          payload[k] = JSON.parse(v);
        } catch {
          errors[k] = "Invalid JSON";
        }
      } else if (Array.isArray(original)) {
        payload[k] = v.trim() ? v.split(",").map((s) => s.trim()).filter(Boolean) : [];
      } else {
        payload[k] = v.trim() || null;
      }
    }
    if (Object.keys(errors).length > 0) {
      setJsonErrors(errors);
      return;
    }
    setJsonErrors({});
    mutation.mutate(payload);
  }

  return (
    <div>
      {canEdit && (
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{title}</h3>
          {editing ? (
            <div className="flex items-center gap-2">
              {mutation.isError && (
                <span className="text-xs text-red-600">{(mutation.error as Error).message}</span>
              )}
              <button
                onClick={() => setEditing(false)}
                className="text-xs text-gray-400 hover:text-gray-700"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={mutation.isPending}
                className="px-2.5 py-1 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
              >
                {mutation.isPending ? "Saving…" : "Save"}
              </button>
            </div>
          ) : (
            <button
              onClick={startEditing}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
            >
              Edit fields
            </button>
          )}
        </div>
      )}

      {fieldRows.length === 0 ? (
        <p className="text-sm text-gray-400">No fields available.</p>
      ) : (
        <dl className="space-y-1">
          {fieldRows.map(([key, value]) => {
            const complex = isComplexValue(value);
            return (
              <div key={key} className="py-2.5 border-b border-gray-100 last:border-0">
                <dt className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                  {labelFor(key)}
                  {editing && complex && (
                    <span className="ml-1 font-normal normal-case text-gray-400">(JSON)</span>
                  )}
                </dt>
                {editing ? (
                  complex ? (
                    <>
                      <textarea
                        value={form[key] ?? ""}
                        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                        rows={6}
                        className="w-full font-mono text-xs rounded-md border border-gray-300 px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                      />
                      {jsonErrors[key] && (
                        <p className="text-xs text-red-500 mt-0.5">{jsonErrors[key]}</p>
                      )}
                    </>
                  ) : (
                    <input
                      type="text"
                      value={form[key] ?? ""}
                      onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                      className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    />
                  )
                ) : (
                  <dd className="text-sm text-gray-800 break-words">{renderFieldValue(value)}</dd>
                )}
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}
