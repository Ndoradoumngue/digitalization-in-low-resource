import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDbDocuments } from "../hooks/useDocumentsDb";
import { useCreateDocumentLink } from "../hooks/useDocumentLinks";
import { summarizeExtra } from "../utils/format";

const RELATION_SUGGESTIONS = ["concerns", "supersedes", "transfers", "amends", "renews"];

interface Props {
  tableName: string;
  id:        string;
  onDone:    () => void;
}

export default function DocumentLinkPicker({ tableName, id, onDone }: Props) {
  const { t } = useTranslation();
  const [inputQ, setInputQ]     = useState("");
  const [q, setQ]               = useState("");
  const [selected, setSelected] = useState<{ tableName: string; id: string; label: string } | null>(null);
  const [relation, setRelation] = useState("");
  const [note, setNote]         = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data, isFetching } = useDbDocuments({ q, page_size: 10 });
  const createMutation = useCreateDocumentLink(tableName, id);

  const results = (data?.results ?? []).filter(
    (r) => !(r.table_name === tableName && r.id === id)
  );

  function handleSearch(value: string) {
    setInputQ(value);
    setSelected(null);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setQ(value), 300);
  }

  function submit() {
    if (!selected || !relation.trim()) return;
    createMutation.mutate(
      { to_table: selected.tableName, to_id: selected.id, relation: relation.trim(), note: note.trim() || undefined },
      { onSuccess: onDone },
    );
  }

  return (
    <div className="mb-3 p-3 rounded-md border border-gray-200 bg-gray-50 space-y-2">
      {!selected ? (
        <>
          <input
            type="text"
            value={inputQ}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={t("linkPicker.searchPlaceholder")}
            autoFocus
            className="w-full h-8 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
          {isFetching && <p className="text-xs text-gray-400">{t("linkPicker.searching")}</p>}
          {q && !isFetching && results.length === 0 && (
            <p className="text-xs text-gray-400">{t("linkPicker.noResults")}</p>
          )}
          {results.length > 0 && (
            <div className="max-h-48 overflow-y-auto rounded-md border border-gray-200 bg-white divide-y divide-gray-100">
              {results.map((r) => (
                <button
                  key={`${r.table_name}-${r.id}`}
                  onClick={() =>
                    setSelected({
                      tableName: r.table_name,
                      id: r.id,
                      label: `${r.document_type ?? r.table_name} — ${summarizeExtra(r.extra_fields, 1)}`,
                    })
                  }
                  className="block w-full text-left px-3 py-2 text-xs hover:bg-indigo-50 transition-colors"
                >
                  <span className="font-semibold text-indigo-700">{r.document_type ?? r.table_name}</span>
                  <span className="text-gray-500 ml-2 truncate">{summarizeExtra(r.extra_fields)}</span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="flex items-center justify-between gap-2 text-xs bg-white rounded-md border border-gray-200 px-3 py-2">
          <span className="truncate text-gray-700">{selected.label}</span>
          <button
            onClick={() => setSelected(null)}
            className="flex-shrink-0 text-gray-400 hover:text-gray-700"
          >
            {t("linkPicker.change")}
          </button>
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          type="text"
          list="relation-suggestions"
          value={relation}
          onChange={(e) => setRelation(e.target.value)}
          placeholder={t("linkPicker.relationPlaceholder")}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <datalist id="relation-suggestions">
          {RELATION_SUGGESTIONS.map((r) => (
            <option key={r} value={r} />
          ))}
        </datalist>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("linkPicker.notePlaceholder")}
          className="h-8 flex-1 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      {createMutation.isError && (
        <p className="text-xs text-red-600">{(createMutation.error as Error).message}</p>
      )}

      <div className="flex justify-end gap-2">
        <button
          onClick={onDone}
          className="px-2.5 py-1.5 rounded-md text-xs border border-gray-300 hover:bg-gray-100 transition-colors"
        >
          {t("linkPicker.cancel")}
        </button>
        <button
          onClick={submit}
          disabled={!selected || !relation.trim() || createMutation.isPending}
          className="px-2.5 py-1.5 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          {createMutation.isPending ? t("linkPicker.linking") : t("linkPicker.addLink")}
        </button>
      </div>
    </div>
  );
}
