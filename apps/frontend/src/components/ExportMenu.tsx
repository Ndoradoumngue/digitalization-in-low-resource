import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useExportDocument } from "../hooks/useDocumentsDb";
import { labelFor } from "../utils/format";

/** Download one document's data: the whole record as JSON or SQL, or one
 *  list field (e.g. "entries") as a CSV spreadsheet. */
export default function ExportMenu({
  tableName,
  id,
  listFields,
}: {
  tableName: string;
  id: string;
  listFields: string[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const exportMutation = useExportDocument(tableName, id);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function run(format: "json" | "sql" | "csv", field?: string) {
    setOpen(false);
    exportMutation.mutate({ format, field });
  }

  const item = "block w-full text-left px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        disabled={exportMutation.isPending}
        title={t("exportMenu.title")}
        className="px-2.5 py-1.5 rounded-md text-xs font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
      >
        {exportMutation.isPending ? t("exportMenu.preparing") : t("exportMenu.button")}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-60 rounded-md border border-gray-200 bg-white shadow-lg z-20 py-1">
          <button className={item} onClick={() => run("json")}>{t("exportMenu.json")}</button>
          <button className={item} onClick={() => run("sql")}>{t("exportMenu.sql")}</button>
          {listFields.length > 0 && <div className="my-1 border-t border-gray-100" />}
          {listFields.map((f) => (
            <button key={f} className={item} onClick={() => run("csv", f)}>
              {t("exportMenu.csv", { field: labelFor(f) })}
            </button>
          ))}
        </div>
      )}
      {exportMutation.isError && (
        <p className="absolute right-0 mt-1 text-xs text-red-500 whitespace-nowrap">
          {(exportMutation.error as Error).message}
        </p>
      )}
    </div>
  );
}
