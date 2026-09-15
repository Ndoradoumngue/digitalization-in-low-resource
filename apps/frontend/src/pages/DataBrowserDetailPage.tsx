import { useState, useEffect } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useDbDocumentDetail } from "../hooks/useDocumentsDb";
import { useAuth } from "../context/AuthContext";
import NavSidebar from "../components/NavSidebar";
import PanZoomImage from "../components/PanZoomImage";
import { dbImageUrl, retryDocument, deleteDocument } from "@sdai/api-client";
import { labelFor } from "../utils/format";
import { renderFieldValue } from "../utils/renderField";
import LinksSection from "../components/LinksSection";
import AccessSection from "../components/AccessSection";
import SeriesSection from "../components/SeriesSection";

// The Ops/QA detail view: every raw column, confidence/review-status
// internals, and pipeline actions (retry a crashed extraction, hard
// delete) — reached only from DataBrowserPage.tsx (/ops/data). For the
// document-management record view regular users see (no QA internals),
// see DocumentDetailPage.tsx at /documents/:tableName/:id.

// ── Types ─────────────────────────────────────────────────────────────────────

interface ListEntry { tableName: string; id: string; }
interface LocationState {
  list?:         ListEntry[];
  currentIndex?: number;
}

// ── Confidence / review helpers ───────────────────────────────────────────────

function tierColor(tier: string) {
  if (tier === "high")   return "bg-emerald-100 text-emerald-800";
  if (tier === "medium") return "bg-amber-100 text-amber-800";
  if (tier === "low")    return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-500";
}

function reviewColor(status: string) {
  if (status === "auto_approved")   return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (status === "review_required") return "bg-amber-50 text-amber-700 border-amber-200";
  if (status === "manual_entry")    return "bg-red-50 text-red-700 border-red-200";
  return "bg-gray-50 text-gray-500 border-gray-200";
}

const SKIP_FIELDS = new Set(["id", "source_image_path", "source_pdf_path", "page_image_paths", "record_id", "series_id"]);

// ── Main component ────────────────────────────────────────────────────────────

export default function DataBrowserDetailPage() {
  const { tableName = "", id = "" } = useParams<{ tableName: string; id: string }>();
  const navigate  = useNavigate();
  const location  = useLocation();
  const state     = (location.state ?? {}) as LocationState;
  const list      = state.list ?? [];
  const idx       = state.currentIndex ?? -1;

  const { user }        = useAuth();
  const queryClient     = useQueryClient();
  const { data, isLoading, error } = useDbDocumentDetail(tableName, id);
  const [actionError, setActionError] = useState<string | null>(null);

  function afterDestructiveAction() {
    queryClient.invalidateQueries({ queryKey: ["db-documents"] });
    queryClient.invalidateQueries({ queryKey: ["db-types"] });
    navigate("/ops/data");
  }

  const retryMutation = useMutation({
    mutationFn: () => retryDocument(tableName, id),
    onSuccess:  afterDestructiveAction,
    onError:    (e: Error) => setActionError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteDocument(tableName, id),
    onSuccess:  afterDestructiveAction,
    onError:    (e: Error) => setActionError(e.message),
  });

  const pageImagePaths = Array.isArray(data?.page_image_paths)
    ? (data.page_image_paths as unknown[]).map(String)
    : [];
  const pdfPath = data?.source_pdf_path ? String(data.source_pdf_path) : null;

  const [activePage, setActivePage] = useState(0);
  useEffect(() => { setActivePage(0); }, [tableName, id]);

  // Key press for prev/next
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft"  && idx > 0)               goTo(idx - 1);
      if (e.key === "ArrowRight" && idx < list.length - 1) goTo(idx + 1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  function goTo(newIdx: number) {
    const entry = list[newIdx];
    if (!entry) return;
    navigate(`/ops/data/${entry.tableName}/${entry.id}`, {
      state: { list, currentIndex: newIdx },
    });
  }

  const imgSrc = pageImagePaths.length > 0
    ? dbImageUrl(pageImagePaths[Math.min(activePage, pageImagePaths.length - 1)])
    : data?.source_image_path
    ? dbImageUrl(String(data.source_image_path))
    : null;

  const confidence   = data?.confidence   ? String(data.confidence)   : null;
  const reviewStatus = data?.review_status ? String(data.review_status) : null;

  // Build display rows — skip internal fields
  const fieldRows = data
    ? Object.entries(data).filter(([k]) => !SKIP_FIELDS.has(k))
    : [];

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-3 flex-shrink-0">
        <button
          onClick={() => navigate("/ops/data")}
          className="text-gray-500 hover:text-gray-800 transition-colors"
          aria-label="Back to data browser"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
        </button>

        <span className="text-sm font-semibold text-gray-700 truncate max-w-xs">
          {tableName.replace(/_/g, " ")}
        </span>

        {typeof data?.record_id === "string" && data.record_id && (
          <span className="inline-flex items-center px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-xs font-mono">
            {data.record_id}
          </span>
        )}

        {confidence && (
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold ${tierColor(confidence)}`}>
            {confidence}
          </span>
        )}

        {reviewStatus && (
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${reviewColor(reviewStatus)}`}>
            {reviewStatus.replace(/_/g, " ")}
          </span>
        )}

        {(reviewStatus === "manual_entry" || user?.role === "admin") && (
          <div className="flex items-center gap-2">
            {reviewStatus === "manual_entry" && (
              <button
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                className="px-2.5 py-1.5 rounded-md text-xs font-medium border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
              >
                {retryMutation.isPending ? "Retrying…" : "Retry"}
              </button>
            )}
            {user?.role === "admin" && (
              <button
                onClick={() => {
                  if (window.confirm("Permanently delete this document? This cannot be undone.")) {
                    deleteMutation.mutate();
                  }
                }}
                disabled={deleteMutation.isPending}
                className="px-2.5 py-1.5 rounded-md text-xs font-medium border border-red-300 text-red-700 hover:bg-red-50 disabled:opacity-50 transition-colors"
              >
                {deleteMutation.isPending ? "Deleting…" : "Delete"}
              </button>
            )}
          </div>
        )}

        {actionError && (
          <span className="text-xs text-red-600 truncate max-w-xs">{actionError}</span>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Prev / Next */}
        {list.length > 0 && (
          <div className="flex items-center gap-1">
            <button
              disabled={idx <= 0}
              onClick={() => goTo(idx - 1)}
              className="px-2.5 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
              title="Previous (←)"
            >
              ← Prev
            </button>
            <span className="text-xs text-gray-500 px-1">
              {idx + 1} / {list.length}
            </span>
            <button
              disabled={idx >= list.length - 1}
              onClick={() => goTo(idx + 1)}
              className="px-2.5 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
              title="Next (→)"
            >
              Next →
            </button>
          </div>
        )}
      </header>

      {/* Body */}
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
          Loading…
        </div>
      ) : error ? (
        <div className="flex-1 p-8 text-sm text-red-500">
          Failed to load document.
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {/* Left — image viewer (55%) */}
          <div className="w-[55%] flex-shrink-0 border-r border-gray-200 flex flex-col">
            {(pageImagePaths.length > 1 || pdfPath) && (
              <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 bg-white flex-shrink-0 overflow-x-auto">
                {pageImagePaths.length > 1 &&
                  pageImagePaths.map((_, i) => (
                    <button
                      key={i}
                      onClick={() => setActivePage(i)}
                      className={`flex-shrink-0 px-2.5 py-1 rounded-md text-xs font-medium border transition-colors ${
                        i === activePage
                          ? "bg-indigo-600 border-indigo-600 text-white"
                          : "border-gray-300 text-gray-600 hover:bg-gray-50"
                      }`}
                    >
                      Page {i + 1}
                    </button>
                  ))}
                {pdfPath && (
                  <a
                    href={dbImageUrl(pdfPath)}
                    target="_blank"
                    rel="noreferrer"
                    className="flex-shrink-0 ml-auto px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
                  >
                    View original PDF ↗
                  </a>
                )}
              </div>
            )}
            <div className="flex-1 overflow-hidden">
              {imgSrc ? (
                <PanZoomImage src={imgSrc} key={imgSrc} />
              ) : (
                <div className="flex h-full items-center justify-center text-gray-400 text-sm bg-gray-100">
                  No image available
                </div>
              )}
            </div>
          </div>

          {/* Right — extracted fields (45%) */}
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-1">
            {fieldRows.length === 0 ? (
              <p className="text-sm text-gray-400">No fields available.</p>
            ) : (
              fieldRows.map(([key, value]) => (
                <div key={key} className="py-2 border-b border-gray-100 last:border-0">
                  <dt className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                    {labelFor(key)}
                  </dt>
                  <dd className="text-sm text-gray-800 break-words">
                    {renderFieldValue(value)}
                  </dd>
                </div>
              ))
            )}
            {tableName && id && <LinksSection tableName={tableName} id={id} basePath="/ops/data" />}
            {tableName && id && (
              <SeriesSection
                tableName={tableName}
                id={id}
                seriesId={typeof data?.series_id === "string" ? data.series_id : null}
              />
            )}
            {tableName && id && <AccessSection tableName={tableName} id={id} />}
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
