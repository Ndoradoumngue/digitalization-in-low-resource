import { useState, useCallback, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDbDocuments, useDbTypes } from "../hooks/useDocumentsDb";
import NavSidebar from "../components/NavSidebar";
import type { DbListParams } from "@sdai/api-client";
import { summarizeExtra } from "../utils/format";

// Raw ops/QA view over the ingested-document tables: confidence tiers,
// review status, table names, precise date-range filtering. For the
// document-management browsing experience (categories, reviewer,
// keyword search, no internal plumbing exposed), see DocumentsPage.tsx.

const CONFIDENCE_OPTIONS = ["", "high", "medium", "low"] as const;
const REVIEW_OPTIONS     = ["", "auto_approved", "review_required", "manual_entry"] as const;
const PAGE_SIZE          = 20;

function tierColor(tier: string | null) {
  if (tier === "high")   return "bg-emerald-100 text-emerald-800";
  if (tier === "medium") return "bg-amber-100 text-amber-800";
  if (tier === "low")    return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-500";
}

function reviewColor(status: string | null) {
  if (status === "auto_approved")   return "bg-emerald-50 text-emerald-700";
  if (status === "review_required") return "bg-amber-50 text-amber-700";
  if (status === "manual_entry")    return "bg-red-50 text-red-700";
  return "bg-gray-50 text-gray-500";
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString(); } catch { return iso; }
}

export default function DataBrowserPage() {
  const navigate                  = useNavigate();
  const { t }                     = useTranslation();
  const [searchParams]            = useSearchParams();
  const { data: types }           = useDbTypes();

  const [page, setPage]               = useState(1);
  const [documentType, setDocumentType] = useState(searchParams.get("document_type") ?? "");
  const [confidence, setConfidence]   = useState("");
  const [reviewStatus, setReviewStatus] = useState("");
  const [dateFrom, setDateFrom]       = useState("");
  const [dateTo, setDateTo]           = useState("");
  const [q, setQ]                     = useState("");
  const [inputQ, setInputQ]           = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const params: DbListParams = {
    page,
    page_size: PAGE_SIZE,
    ...(documentType  && { document_type:  documentType }),
    ...(confidence    && { confidence }),
    ...(reviewStatus  && { review_status:  reviewStatus }),
    ...(dateFrom      && { date_from:      dateFrom }),
    ...(dateTo        && { date_to:        dateTo }),
    ...(q             && { q }),
  };

  const { data, isLoading, error } = useDbDocuments(params);

  const handleSearch = useCallback((value: string) => {
    setInputQ(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setQ(value);
      setPage(1);
    }, 300);
  }, []);

  const handleFilter = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const rows       = data?.results ?? [];
  const total      = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4 flex-shrink-0">
        <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("databrowser.header")}</h1>
        {total > 0 && (
          <span className="ml-1 text-xs text-gray-500">{t("databrowser.count", { count: total })}</span>
        )}
      </header>

      {/* Filter bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-3 flex flex-wrap items-center gap-3 flex-shrink-0">
        {/* Search */}
        <input
          type="text"
          value={inputQ}
          onChange={(e) => handleSearch(e.target.value)}
          placeholder={t("databrowser.searchPlaceholder")}
          className="h-8 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 w-56"
        />

        {/* Type */}
        <select
          value={documentType}
          onChange={(e) => handleFilter(() => setDocumentType(e.target.value))}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="">{t("databrowser.allTypes")}</option>
          {(types ?? []).map((t) => (
            <option key={t.table_name} value={t.table_name}>
              {t.document_type} ({t.count})
            </option>
          ))}
        </select>

        {/* Confidence */}
        <select
          value={confidence}
          onChange={(e) => handleFilter(() => setConfidence(e.target.value))}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="">{t("databrowser.allConfidence")}</option>
          {CONFIDENCE_OPTIONS.filter(Boolean).map((c) => (
            <option key={c} value={c}>{t(`databrowser.confidence.${c}`)}</option>
          ))}
        </select>

        {/* Review status */}
        <select
          value={reviewStatus}
          onChange={(e) => handleFilter(() => setReviewStatus(e.target.value))}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <option value="">{t("databrowser.allStatuses")}</option>
          {REVIEW_OPTIONS.filter(Boolean).map((s) => (
            <option key={s} value={s}>{t(`databrowser.reviewStatus.${s}`)}</option>
          ))}
        </select>

        {/* Date range */}
        <label className="flex items-center gap-1 text-xs text-gray-500">
          {t("databrowser.from")}
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => handleFilter(() => setDateFrom(e.target.value))}
            className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-gray-500">
          {t("databrowser.to")}
          <input
            type="date"
            value={dateTo}
            onChange={(e) => handleFilter(() => setDateTo(e.target.value))}
            className="h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </label>

        {/* Reset */}
        {(documentType || confidence || reviewStatus || dateFrom || dateTo || q) && (
          <button
            onClick={() => {
              setDocumentType(""); setConfidence(""); setReviewStatus("");
              setDateFrom(""); setDateTo(""); setQ(""); setInputQ(""); setPage(1);
            }}
            className="h-8 px-3 rounded-md text-xs text-gray-500 border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            {t("databrowser.reset")}
          </button>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {error ? (
          <div className="p-8 text-sm text-red-500">{t("databrowser.loadError")}</div>
        ) : isLoading && rows.length === 0 ? (
          <div className="p-8 space-y-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-10 bg-gray-200 rounded animate-pulse" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-sm text-gray-400">{t("databrowser.noResults")}</div>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 bg-gray-100 z-10">
              <tr>
                {[
                  t("databrowser.colType"), t("databrowser.colDetails"), t("databrowser.colConfidence"),
                  t("databrowser.colReviewStatus"), t("databrowser.colIngested"),
                ].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left text-xs font-semibold text-gray-600 whitespace-nowrap border-b border-gray-200">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={`${row.table_name}-${row.id}`}
                  onClick={() =>
                    navigate(`/ops/data/${row.table_name}/${row.id}`, {
                      state: {
                        list: rows.map((r) => ({ tableName: r.table_name, id: r.id })),
                        currentIndex: i,
                        filters: params,
                      },
                    })
                  }
                  className="cursor-pointer hover:bg-indigo-50 border-b border-gray-100 transition-colors"
                >
                  <td className="px-4 py-2.5">
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-indigo-100 text-indigo-800">
                      {row.document_type ?? row.table_name}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-gray-700 max-w-[360px] truncate" title={summarizeExtra(row.extra_fields, 10)}>
                    {summarizeExtra(row.extra_fields)}
                  </td>
                  <td className="px-4 py-2.5">
                    {row.confidence ? (
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${tierColor(row.confidence)}`}>
                        {t(`databrowser.confidence.${row.confidence}`, row.confidence)}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    {row.review_status ? (
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${reviewColor(row.review_status)}`}>
                        {t(`databrowser.reviewStatus.${row.review_status}`, row.review_status.replace(/_/g, " "))}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-gray-500 whitespace-nowrap text-xs">
                    {fmtDate(row.ingested_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="bg-white border-t border-gray-200 px-6 py-3 flex items-center justify-between flex-shrink-0">
          <span className="text-xs text-gray-500">
            {t("databrowser.pagination", { page, totalPages, total: total.toLocaleString() })}
          </span>
          <div className="flex items-center gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              {t("databrowser.previous")}
            </button>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              {t("databrowser.next")}
            </button>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
