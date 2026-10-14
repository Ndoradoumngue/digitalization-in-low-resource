import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useDbDocuments, useDbReviewers, useDbTypes } from "../hooks/useDocumentsDb";
import { useSeries } from "../hooks/useSeries";
import { useTags } from "../hooks/useTags";
import SeriesTree from "../components/SeriesTree";
import { seriesLabel } from "../utils/series";
import NavSidebar from "../components/NavSidebar";
import type { DbListParams } from "@sdai/api-client";
import { summarizeExtra } from "../utils/format";
import SearchSnippet from "../components/SearchSnippet";
import type { DbDocument, ExportDocumentRef, ExportRequest } from "@sdai/types";
import { useCreateExport } from "../hooks/useSharing";
import { useCreateLegalHold } from "../hooks/useLifecycle";
import LifecycleBadge from "../components/LifecycleBadge";
import { useAuth } from "../context/AuthContext";
import { canManageLifecycle } from "../utils/permissions";

// The real document-browsing experience: categories, reviewer, keyword
// search - no confidence/review_status/table-name plumbing shown (that's
// what DataBrowserPage.tsx, under Ops, is for). Both pages share the same
// document detail route (/documents/:tableName/:id).

const PAGE_SIZE = 24;
// "page N" badges shown per search result before collapsing into "+N".
const MAX_PAGE_BADGES = 6;

function fmtDate(iso: string | null) {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleDateString(); } catch { return iso; }
}

export default function DocumentsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { data: types } = useDbTypes();
  const { data: reviewers } = useDbReviewers();
  const { data: series } = useSeries();

  const [page, setPage] = useState(1);
  const [category, setCategory] = useState("");
  const [reviewedBy, setReviewedBy] = useState("");
  const [seriesId, setSeriesId] = useState("");
  const [includeSubseries, setIncludeSubseries] = useState(true);
  const [tagId, setTagId] = useState("");
  const { data: tags } = useTags();
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  // The search lives in the URL (?q=) so the nav's global search box, the
  // back button and a shared link all land on the same results.
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get("q") ?? "";
  const [inputQ, setInputQ] = useState(q);
  useEffect(() => { setInputQ(q); setPage(1); }, [q]);
  const setQ = useCallback((value: string) => {
    setSearchParams(value ? { q: value } : {}, { replace: true });
  }, [setSearchParams]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const params: DbListParams = {
    page,
    page_size: PAGE_SIZE,
    ...(category && { document_type: category }),
    ...(reviewedBy && { reviewed_by: reviewedBy }),
    ...(seriesId && { series: seriesId, include_subseries: includeSubseries }),
    ...(tagId && { tag: tagId }),
    ...(dateFrom && { date_from: dateFrom }),
    ...(dateTo && { date_to: dateTo }),
    ...(q && { q }),
  };

  const { data, isLoading, error } = useDbDocuments(params);
  const { user } = useAuth();

  // Multi-select for a selective export - kept across pages and filters
  // until cleared, keyed "<table>/<id>".
  const [selected, setSelected] = useState<Map<string, ExportDocumentRef>>(new Map());
  const [exportVersions, setExportVersions] = useState<"current" | "all">("current");
  const createExport = useCreateExport();
  const toggleSelected = useCallback((row: DbDocument) => {
    setSelected((prev) => {
      const next = new Map(prev);
      const key = `${row.table_name}/${row.id}`;
      if (next.has(key)) next.delete(key);
      else next.set(key, { table_name: row.table_name, id: row.id });
      return next;
    });
  }, []);
  // The current search, as export and legal-hold selections take it.
  const searchSelection = {
    ...(q && { q }),
    ...(category && { document_type: category }),
    ...(reviewedBy && { reviewed_by: reviewedBy }),
    ...(seriesId && { series: seriesId, include_subseries: includeSubseries }),
    ...(tagId && { tag: tagId }),
    ...(dateFrom && { date_from: dateFrom }),
    ...(dateTo && { date_to: dateTo }),
  };
  function exportSelection(kind: "selected" | "results") {
    const body: ExportRequest = kind === "selected"
      ? { documents: [...selected.values()], versions: exportVersions }
      : { versions: exportVersions, search: searchSelection };
    createExport.mutate(body, { onSuccess: () => { if (kind === "selected") setSelected(new Map()); } });
  }

  // Legal hold on every document the current search finds (frozen list).
  const createHold = useCreateLegalHold();
  const [holdReason, setHoldReason] = useState<string | null>(null);
  function holdResults() {
    if (!holdReason || holdReason.trim().length < 3) return;
    createHold.mutate(
      { search: searchSelection, reason: holdReason.trim() },
      { onSuccess: () => setHoldReason(null) },
    );
  }

  const reviewerName = useMemo(() => {
    const map = new Map<string, string>();
    for (const r of reviewers ?? []) map.set(r.id, r.full_name || r.email);
    return map;
  }, [reviewers]);

  const seriesName = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of series ?? []) map.set(s.id, seriesLabel(s, t, false));
    return map;
  }, [series, t]);

  const handleSearch = useCallback((value: string) => {
    setInputQ(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setQ(value.trim());
      setPage(1);
    }, 300);
  }, [setQ]);

  function selectCategory(tableName: string) {
    setCategory((c) => (c === tableName ? "" : tableName));
    setPage(1);
  }

  const rows = data?.results ?? [];

  // Opens the document; `pageNo` (1-based) opens the viewer at that page.
  function open(row: DbDocument, i: number, pageNo?: number) {
    navigate(`/documents/${row.table_name}/${row.id}${pageNo ? `?page=${pageNo}` : ""}`, {
      state: {
        list: rows.map((r) => ({ tableName: r.table_name, id: r.id })),
        currentIndex: i,
        filters: params,
      },
    });
  }
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = category || reviewedBy || seriesId || tagId || dateFrom || dateTo || q;

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

          {tags && tags.length > 0 && (
            <select
              value={tagId}
              onChange={(e) => { setTagId(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label={t("tags.filter")}
            >
              <option value="">{t("tags.all")}</option>
              {tags.map((tag) => (
                <option key={tag.id} value={tag.id}>{tag.name}</option>
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
                setCategory(""); setReviewedBy(""); setSeriesId(""); setTagId("");
                setDateFrom(""); setDateTo("");
                setQ(""); setInputQ(""); setPage(1);
              }}
              className="h-9 px-3 rounded-md text-xs text-gray-500 border border-gray-300 hover:bg-gray-50 transition-colors"
            >
              {t("documents.reset")}
            </button>
          )}
        </div>

        {/* Category chips - "browse by folder" */}
        {types && types.length > 0 && (
          <div className="bg-white border-b border-gray-200 px-6 py-3 flex flex-wrap gap-2 flex-shrink-0">
            {types.map((t) => (
              <button
                key={t.table_name}
                onClick={() => selectCategory(t.table_name)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${category === t.table_name
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

        {/* Selective export: the checked documents, or every current result */}
        {(rows.length > 0 || selected.size > 0) && (
          <div className="bg-white border-b border-gray-200 px-6 py-2 flex flex-wrap items-center gap-3 flex-shrink-0 text-xs">
            <span className="text-gray-600">{t("exports.selected", { count: selected.size })}</span>
            {selected.size > 0 && (
              <button onClick={() => setSelected(new Map())} className="text-gray-500 hover:text-gray-800 underline">
                {t("exports.clearSelection")}
              </button>
            )}
            <div className="flex-1" />
            <label className="flex items-center gap-1 text-gray-500">
              {t("exports.versionsLabel")}
              <select
                value={exportVersions}
                onChange={(e) => setExportVersions(e.target.value as "current" | "all")}
                className="h-7 rounded-md border border-gray-300 px-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="current">{t("exports.versions.current")}</option>
                <option value="all">{t("exports.versions.all")}</option>
              </select>
            </label>
            <button
              onClick={() => exportSelection("selected")}
              disabled={selected.size === 0 || createExport.isPending}
              className="h-7 px-2.5 rounded-md font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
            >
              {t("exports.exportSelected", { count: selected.size })}
            </button>
            <button
              onClick={() => exportSelection("results")}
              disabled={total === 0 || createExport.isPending}
              className="h-7 px-2.5 rounded-md font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
            >
              {t("exports.exportResults", { count: total })}
            </button>
            {canManageLifecycle(user) && total > 0 && holdReason === null && (
              <button
                onClick={() => setHoldReason("")}
                className="h-7 px-2.5 rounded-md font-medium border border-purple-300 text-purple-800 hover:bg-purple-50"
              >
                {t("legalHolds.holdResults", { count: total })}
              </button>
            )}
            {holdReason !== null && (
              <span className="w-full flex items-center gap-2">
                <input
                  value={holdReason}
                  onChange={(e) => setHoldReason(e.target.value)}
                  placeholder={t("lifecycle.reasonRequired")}
                  className="h-7 flex-1 rounded-md border border-gray-300 px-2 text-xs"
                  autoFocus
                />
                <button onClick={() => setHoldReason(null)} className="text-gray-500">{t("lifecycle.cancel")}</button>
                <button
                  onClick={holdResults}
                  disabled={holdReason.trim().length < 3 || createHold.isPending}
                  className="h-7 px-2.5 rounded-md font-semibold bg-purple-700 text-white disabled:opacity-40"
                >
                  {t("legalHolds.holdResults", { count: total })}
                </button>
              </span>
            )}
            {createHold.isSuccess && holdReason === null && (
              <span role="status" className="w-full text-purple-800">
                {t("legalHolds.created", { count: createHold.data?.document_count ?? 0 })}
              </span>
            )}
            {createHold.isError && (
              <span role="alert" className="w-full text-red-600">{(createHold.error as Error).message}</span>
            )}
            {createExport.isSuccess && (
              <span role="status" className="w-full text-emerald-700">
                {t("exports.started")}{" "}
                <Link to="/exports" className="font-semibold underline">{t("exports.seeExports")}</Link>
              </span>
            )}
            {createExport.isError && (
              <span role="alert" className="w-full text-red-600">{(createExport.error as Error).message}</span>
            )}
          </div>
        )}

        {/* Plan de classement + results */}
        <div className="flex-1 flex overflow-hidden">
        <SeriesTree
          selectedId={seriesId}
          includeSubseries={includeSubseries}
          onSelect={(id) => { setSeriesId(id); setPage(1); }}
          onIncludeSubseriesChange={(value) => { setIncludeSubseries(value); setPage(1); }}
        />
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
                <div
                  key={`${row.table_name}-${row.id}`}
                  role="link"
                  tabIndex={0}
                  onClick={() => open(row, i, defaultPage(row))}
                  onKeyDown={(e) => { if (e.key === "Enter") open(row, i, defaultPage(row)); }}
                  className="cursor-pointer text-left bg-white rounded-lg border border-gray-200 p-4 hover:border-indigo-300 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 transition-all flex flex-col gap-2"
                >
                  <div className="flex items-center gap-2">
                    <span className="inline-flex self-start items-center px-2 py-0.5 rounded text-xs font-semibold bg-indigo-100 text-indigo-800">
                      {row.document_type ?? row.table_name}
                    </span>
                    {row.record_id && (
                      <span className="text-[11px] font-mono text-gray-400 truncate">{row.record_id}</span>
                    )}
                    <LifecycleBadge status={row.lifecycle_status} legalHold={row.legal_hold} />
                    {row.series_id && seriesName.get(row.series_id) && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-teal-100 text-teal-800 truncate">
                        {seriesName.get(row.series_id)}
                      </span>
                    )}
                    <input
                      type="checkbox"
                      checked={selected.has(`${row.table_name}/${row.id}`)}
                      onChange={() => toggleSelected(row)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                      aria-label={t("exports.selectDocument")}
                      className="ml-auto h-4 w-4 flex-shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-400"
                    />
                  </div>
                  <p className="text-sm text-gray-700 line-clamp-3">
                    {summarizeExtra(row.extra_fields, 4)}
                  </p>
                  {row.search && <SearchHit row={row} onPage={(n) => open(row, i, n)} />}
                  <div className="mt-auto flex items-center justify-between text-xs text-gray-400 pt-1">
                    <span>
                      {row.reviewed_by
                        ? t("documents.reviewedBy", { name: reviewerName.get(row.reviewed_by) ?? "-" })
                        : t("documents.notYetReviewed")}
                    </span>
                    <span>{fmtDate(row.ingested_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
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

// Where a search found the document: the matching extracted fields and/or
// the pages whose text matched (each badge opens the viewer at that page).
function SearchHit({ row, onPage }: { row: DbDocument; onPage: (page: number) => void }) {
  const { t } = useTranslation();
  const search = row.search!;
  const pageSnippet = search.pages.find((p) => p.snippet && p.snippet.length > 0);
  const extra = search.pages.length - MAX_PAGE_BADGES;
  return (
    <div className="flex flex-col gap-1.5 border-t border-gray-100 pt-2">
      {search.snippet.length > 0 ? (
        <SearchSnippet segments={search.snippet} className="line-clamp-3" />
      ) : pageSnippet ? (
        <SearchSnippet segments={pageSnippet.snippet!} className="line-clamp-3" />
      ) : null}
      {search.pages.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[11px] text-gray-400">{t("search.foundOnPages", { count: search.pages.length })}</span>
          {search.pages.slice(0, MAX_PAGE_BADGES).map((p) => (
            <button
              key={p.page}
              type="button"
              onClick={(e) => { e.stopPropagation(); onPage(p.page); }}
              onKeyDown={(e) => e.stopPropagation()}
              title={t("search.openAtPage", { page: p.page })}
              className="px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-[11px] font-medium text-amber-800 hover:bg-amber-100"
            >
              {t("search.pageBadge", { page: p.page })}
            </button>
          ))}
          {extra > 0 && <span className="text-[11px] text-gray-400">{t("search.morePages", { count: extra })}</span>}
        </div>
      )}
    </div>
  );
}

// A result found only in its page text opens at the first matching page.
function defaultPage(row: DbDocument): number | undefined {
  if (!row.search || row.search.snippet.length > 0) return undefined;
  return row.search.pages[0]?.page;
}
