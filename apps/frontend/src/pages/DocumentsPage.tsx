import { useMemo, useState, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDbDocuments, useDbReviewers, useDbTypes } from "../hooks/useDocumentsDb";
import { useSeries } from "../hooks/useSeries";
import NavSidebar from "../components/NavSidebar";
import type { DbListParams } from "@sdai/api-client";
import { summarizeExtra } from "../utils/format";

// The real document-browsing experience: categories, reviewer, keyword
// search — no confidence/review_status/table-name plumbing shown (that's
// what DataBrowserPage.tsx, under Ops, is for). Both pages share the same
// document detail route (/documents/:tableName/:id).

const PAGE_SIZE = 24;

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString(); } catch { return iso; }
}

export default function DocumentsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { data: types }     = useDbTypes();
  const { data: reviewers } = useDbReviewers();
  const { data: series }    = useSeries();

  const [page, setPage]             = useState(1);
  const [category, setCategory]     = useState("");
  const [reviewedBy, setReviewedBy] = useState("");
  const [seriesId, setSeriesId]     = useState("");
  const [dateFrom, setDateFrom]     = useState("");
  const [dateTo, setDateTo]         = useState("");
  const [q, setQ]                   = useState("");
  const [inputQ, setInputQ]         = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const params: DbListParams = {
    page,
    page_size: PAGE_SIZE,
    ...(category    && { document_type: category }),
    ...(reviewedBy  && { reviewed_by:   reviewedBy }),
    ...(seriesId    && { series:        seriesId }),
    ...(dateFrom    && { date_from:     dateFrom }),
    ...(dateTo      && { date_to:       dateTo }),
    ...(q           && { q }),
  };

  const { data, isLoading, error } = useDbDocuments(params);

  const reviewerName = useMemo(() => {
    const map = new Map<string, string>();
    for (const r of reviewers ?? []) map.set(r.id, r.full_name || r.email);
    return map;
  }, [reviewers]);

  const seriesName = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of series ?? []) map.set(s.id, s.name);
    return map;
  }, [series]);

  const handleSearch = useCallback((value: string) => {
    setInputQ(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setQ(value);
      setPage(1);
    }, 300);
  }, []);

  function selectCategory(tableName: string) {
    setCategory((c) => (c === tableName ? "" : tableName));
    setPage(1);
  }

  const rows       = data?.results ?? [];
  const total      = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = category || reviewedBy || seriesId || dateFrom || dateTo || q;

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4 flex-shrink-0">
          <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("documents.header")}</h1>
          {total > 0 && (
            <span className="ml-1 text-xs text-gray-500">{t("documents.count", { count: total })}</span>
          )}
        </header>

        {/* Search + reviewer + date range */}
        <div className="bg-white border-b border-gray-200 px-6 py-3 flex flex-wrap items-center gap-3 flex-shrink-0">
          <input
            type="text"
            value={inputQ}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={t("documents.searchPlaceholder")}
            className="h-9 flex-1 min-w-[220px] max-w-md rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />

          <select
            value={reviewedBy}
            onChange={(e) => { setReviewedBy(e.target.value); setPage(1); }}
            className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            <option value="">{t("documents.allReviewers")}</option>
            {(reviewers ?? []).map((r) => (
              <option key={r.id} value={r.id}>{r.full_name || r.email}</option>
            ))}
          </select>

          {series && series.length > 0 && (
            <select
              value={seriesId}
              onChange={(e) => { setSeriesId(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <option value="">{t("documents.allSeries")}</option>
              {series.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          )}

          <label className="flex items-center gap-1 text-xs text-gray-500">
            {t("documents.from")}
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>
          <label className="flex items-center gap-1 text-xs text-gray-500">
            {t("documents.to")}
            <input
              type="date"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </label>

          {hasFilters && (
            <button
              onClick={() => {
                setCategory(""); setReviewedBy(""); setSeriesId("");
                setDateFrom(""); setDateTo("");
                setQ(""); setInputQ(""); setPage(1);
              }}
              className="h-9 px-3 rounded-md text-xs text-gray-500 border border-gray-300 hover:bg-gray-50 transition-colors"
            >
              {t("documents.reset")}
            </button>
          )}
        </div>

        {/* Category chips — "browse by folder" */}
        {types && types.length > 0 && (
          <div className="bg-white border-b border-gray-200 px-6 py-3 flex flex-wrap gap-2 flex-shrink-0">
            {types.map((t) => (
              <button
                key={t.table_name}
                onClick={() => selectCategory(t.table_name)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
                  category === t.table_name
                    ? "bg-indigo-600 border-indigo-600 text-white"
                    : "bg-white border-gray-300 text-gray-700 hover:bg-indigo-50"
                }`}
              >
                {t.document_type}
                <span className={category === t.table_name ? "text-indigo-100" : "text-gray-400"}>
                  {t.count}
                </span>
              </button>
            ))}
          </div>
        )}

        {/* Results */}
        <div className="flex-1 overflow-auto p-6">
          {error ? (
            <div className="text-sm text-red-500">{t("documents.loadError")}</div>
          ) : isLoading && rows.length === 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-28 bg-gray-200 rounded-lg animate-pulse" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="text-sm text-gray-400">{t("documents.noResults")}</div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {rows.map((row, i) => (
                <button
                  key={`${row.table_name}-${row.id}`}
                  onClick={() =>
                    navigate(`/documents/${row.table_name}/${row.id}`, {
                      state: {
                        list: rows.map((r) => ({ tableName: r.table_name, id: r.id })),
                        currentIndex: i,
                        filters: params,
                      },
                    })
                  }
                  className="text-left bg-white rounded-lg border border-gray-200 p-4 hover:border-indigo-300 hover:shadow-sm transition-all flex flex-col gap-2"
                >
                  <div className="flex items-center gap-2">
                    <span className="inline-flex self-start items-center px-2 py-0.5 rounded text-xs font-semibold bg-indigo-100 text-indigo-800">
                      {row.document_type ?? row.table_name}
                    </span>
                    {row.record_id && (
                      <span className="text-[11px] font-mono text-gray-400 truncate">{row.record_id}</span>
                    )}
                    {row.series_id && seriesName.get(row.series_id) && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-teal-100 text-teal-800 truncate">
                        {seriesName.get(row.series_id)}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-700 line-clamp-3">
                    {summarizeExtra(row.extra_fields, 4)}
                  </p>
                  <div className="mt-auto flex items-center justify-between text-xs text-gray-400 pt-1">
                    <span>
                      {row.reviewed_by
                        ? t("documents.reviewedBy", { name: reviewerName.get(row.reviewed_by) ?? "—" })
                        : t("documents.notYetReviewed")}
                    </span>
                    <span>{fmtDate(row.ingested_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="bg-white border-t border-gray-200 px-6 py-3 flex items-center justify-between flex-shrink-0">
            <span className="text-xs text-gray-500">
              {t("documents.pagination", { page, totalPages, total: total.toLocaleString() })}
            </span>
            <div className="flex items-center gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
              >
                {t("documents.previous")}
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
              >
                {t("documents.next")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
