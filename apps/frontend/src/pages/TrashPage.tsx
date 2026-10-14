import { useState } from "react";
import { useTranslation } from "react-i18next";
import NavSidebar from "../components/NavSidebar";
import LifecycleBadge from "../components/LifecycleBadge";
import { useRestoreFromTrash, useTrash } from "../hooks/useLifecycle";
import { formatDate, formatDateTime } from "../utils/lifecycle";

// "Corbeille": documents moved to the trash, restorable until the daily job
// purges them (a destruction with an automatic approval record).
export default function TrashPage() {
  const { t, i18n } = useTranslation();
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useTrash(page);
  const restore = useRestoreFromTrash();
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const items = data?.items ?? [];
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0">
          <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("trash.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {t("trash.intro", { days: data?.retention_days ?? 30 })}
          </p>
        </header>
        <div className="flex-1 overflow-auto p-6">
          {restoreError && (
            <p role="alert" className="mb-3 text-sm text-red-600">{restoreError}</p>
          )}
          {error ? (
            <p className="text-sm text-red-500">{t("trash.loadError")}</p>
          ) : isLoading ? (
            <p className="text-sm text-gray-400">{t("trash.loading")}</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-gray-400">{t("trash.empty")}</p>
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                  <tr>
                    <th className="text-left px-4 py-2">{t("trash.document")}</th>
                    <th className="text-left px-4 py-2">{t("trash.deleted")}</th>
                    <th className="text-left px-4 py-2">{t("trash.reason")}</th>
                    <th className="text-left px-4 py-2">{t("trash.purgeAt")}</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {items.map((item) => (
                    <tr key={`${item.table_name}/${item.id}`}>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-xs text-gray-600">{item.record_id ?? "-"}</span>
                          <LifecycleBadge status={item.lifecycle_status} legalHold={item.legal_hold} />
                        </div>
                        <div className="text-gray-800">{item.display ?? item.document_type ?? item.table_name}</div>
                        <div className="text-xs text-gray-400">{item.document_type}</div>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        <div>{formatDateTime(item.deleted_at, i18n.language)}</div>
                        <div className="text-gray-400">{item.deleted_by}</div>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {/* system reasons are codes (ingest deletes), people's are free text */}
                        {item.deleted_reason ? t(`trash.reasons.${item.deleted_reason}`, item.deleted_reason) : ""}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {item.legal_hold ? t("trash.heldNoPurge") : formatDate(item.purge_at, i18n.language)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {item.restorable && (
                          <button
                            onClick={() => {
                              setRestoreError(null);
                              restore.mutate(
                                { tableName: item.table_name, id: item.id },
                                { onError: (e) => setRestoreError((e as Error).message) },
                              );
                            }}
                            disabled={restore.isPending}
                            className="px-3 py-1 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {t("trash.restore")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pages > 1 && (
            <div className="mt-3 flex items-center gap-2 text-xs text-gray-600">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="px-2 py-1 rounded border border-gray-300 disabled:opacity-40">
                ‹
              </button>
              <span>{t("trash.page", { page, pages })}</span>
              <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="px-2 py-1 rounded border border-gray-300 disabled:opacity-40">
                ›
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
