import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useAuditLog } from "../hooks/useAudit";
import NavSidebar from "../components/NavSidebar";
import type { AuditLogEntry } from "@sdai/types";

// ── Action badge ──────────────────────────────────────────────────────────────

const ACTION_STYLES: Record<string, string> = {
  document_approved: "bg-emerald-100 text-emerald-800",
  document_rejected: "bg-red-100    text-red-800",
  document_flagged:  "bg-amber-100  text-amber-800",
  document_ingested: "bg-blue-100   text-blue-800",
  user_login:        "bg-indigo-100 text-indigo-800",
  user_logout:       "bg-gray-100   text-gray-600",
  schema_created:    "bg-purple-100 text-purple-800",
  schema_altered:    "bg-yellow-100 text-yellow-800",
  document_linked:          "bg-cyan-100   text-cyan-800",
  document_link_removed:    "bg-gray-100   text-gray-600",
  document_access_granted:  "bg-amber-100  text-amber-800",
  document_access_revoked:  "bg-gray-100   text-gray-600",
  series_created:           "bg-teal-100   text-teal-800",
  series_assigned:          "bg-teal-100   text-teal-800",
  integrity_check_failed:   "bg-red-100    text-red-800",
  document_fields_edited:   "bg-violet-100 text-violet-800",
  archive_exported:         "bg-indigo-100 text-indigo-800",
};

const ALL_ACTIONS = Object.keys(ACTION_STYLES);

function ActionBadge({ action }: { action: string }) {
  const { t } = useTranslation();
  const cls = ACTION_STYLES[action] ?? "bg-gray-100 text-gray-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {t(`audit.actions.${action}`, action.replace(/_/g, " "))}
    </span>
  );
}

// ── CSV export ────────────────────────────────────────────────────────────────

function exportCsv(entries: AuditLogEntry[]) {
  const HEADERS = [
    "timestamp", "user_email", "action",
    "table_name", "document_id", "details", "ip_address",
  ];
  const escape = (v: unknown) =>
    `"${String(v ?? "").replace(/"/g, '""')}"`;

  const rows = entries.map((e) => [
    e.created_at,
    e.user_email ?? "",
    e.action,
    e.table_name ?? "",
    e.document_id ?? "",
    e.details ? JSON.stringify(e.details) : "",
    e.ip_address ?? "",
  ].map(escape).join(","));

  const csv  = [HEADERS.join(","), ...rows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `audit_log_${new Date().toISOString().slice(0, 10)}.csv`;
  // See useExportArchive's comment — the anchor must be attached to the
  // DOM for click() to work reliably everywhere, and revoking the object
  // URL must be deferred so it can't race the browser starting the
  // download.
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Detail cell ───────────────────────────────────────────────────────────────

function DetailCell({ details }: { details: Record<string, unknown> | null }) {
  if (!details) return <span className="text-gray-400">—</span>;
  const entries = Object.entries(details);
  if (entries.length === 0) return <span className="text-gray-400">—</span>;
  return (
    <div className="space-y-0.5">
      {entries.map(([k, v]) => (
        <div key={k} className="text-xs text-gray-600">
          <span className="font-medium text-gray-500">{k}: </span>
          {Array.isArray(v)
            ? v.join(", ") || "—"
            : String(v ?? "—")}
        </div>
      ))}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const { t } = useTranslation();
  const [page,     setPage]     = useState(1);
  const [action,   setAction]   = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo,   setDateTo]   = useState("");

  const PAGE_SIZE = 50;

  const { data, isLoading, error } = useAuditLog({
    page,
    page_size: PAGE_SIZE,
    action:    action   || undefined,
    date_from: dateFrom || undefined,
    date_to:   dateTo   || undefined,
  });

  const resetFilters = useCallback(() => {
    setAction("");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  }, []);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;

  function formatTs(iso: string) {
    return new Date(iso).toLocaleString("fr-TD", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-4 flex-shrink-0">
        <div className="flex-1">
          <h1 className="text-lg font-bold text-gray-900">{t("audit.header")}</h1>
          {data && (
            <p className="text-xs text-gray-500 mt-0.5">
              {t("audit.eventsRecorded", { count: data.total.toLocaleString() })}
            </p>
          )}
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={action}
            onChange={(e) => { setAction(e.target.value); setPage(1); }}
            className="text-sm rounded-lg border border-gray-300 px-3 py-1.5 bg-white focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
          >
            <option value="">{t("audit.allActions")}</option>
            {ALL_ACTIONS.map((a) => (
              <option key={a} value={a}>{t(`audit.actions.${a}`, a.replace(/_/g, " "))}</option>
            ))}
          </select>

          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
            className="text-sm rounded-lg border border-gray-300 px-3 py-1.5 focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
            placeholder={t("audit.fromPlaceholder")}
          />

          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
            className="text-sm rounded-lg border border-gray-300 px-3 py-1.5 focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
            placeholder={t("audit.toPlaceholder")}
          />

          {(action || dateFrom || dateTo) && (
            <button
              onClick={resetFilters}
              className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
            >
              {t("audit.clear")}
            </button>
          )}

          <button
            onClick={() => data && exportCsv(data.items)}
            disabled={!data || data.items.length === 0}
            className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            {t("audit.exportCsv")}
          </button>
        </div>
      </header>

      {/* ── Table ───────────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">
        {error ? (
          <div className="p-8 text-sm text-red-500">
            {t("audit.loadError")}
          </div>
        ) : isLoading ? (
          <div className="p-8 space-y-3">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-12 bg-gray-200 rounded animate-pulse" />
            ))}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <svg className="w-10 h-10 opacity-40" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25Z" />
            </svg>
            <p className="text-sm">{t("audit.noMatches")}</p>
          </div>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead className="bg-gray-50 border-b border-gray-200 sticky top-0">
              <tr>
                {[
                  t("audit.colTimestamp"), t("audit.colUser"), t("audit.colAction"),
                  t("audit.colTable"), t("audit.colDocument"), t("audit.colDetails"), t("audit.colIp"),
                ].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.items.map((entry) => (
                <tr key={entry.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap font-mono">
                    {formatTs(entry.created_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-700 max-w-[160px] truncate">
                    {entry.user_email ?? (
                      <span className="text-gray-400 italic">{t("audit.systemUser")}</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <ActionBadge action={entry.action} />
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600 font-mono">
                    {entry.table_name ?? <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500 font-mono max-w-[100px]">
                    {entry.document_id
                      ? (
                        <span title={entry.document_id}>
                          {entry.document_id.slice(0, 8)}…
                        </span>
                      )
                      : <span className="text-gray-400">—</span>
                    }
                  </td>
                  <td className="px-4 py-3 max-w-[240px]">
                    <DetailCell details={entry.details} />
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400 font-mono">
                    {entry.ip_address ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Pagination ───────────────────────────────────────────────────────── */}
      {data && data.total > PAGE_SIZE && (
        <footer className="bg-white border-t border-gray-200 px-6 py-3 flex items-center justify-between flex-shrink-0">
          <p className="text-xs text-gray-500">
            {t("audit.showingRange", {
              from: ((page - 1) * PAGE_SIZE) + 1,
              to: Math.min(page * PAGE_SIZE, data.total),
              total: data.total.toLocaleString(),
            })}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {t("audit.prevPage")}
            </button>
            <span className="text-xs text-gray-500 px-2">
              {t("audit.pageOf", { page, totalPages })}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {t("audit.nextPage")}
            </button>
          </div>
        </footer>
      )}
      </div>
    </div>
  );
}
