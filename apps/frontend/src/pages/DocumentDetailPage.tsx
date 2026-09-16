import { useState, useEffect } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useDbDocumentDetail } from "../hooks/useDocumentsDb";
import { useAuth } from "../context/AuthContext";
import NavSidebar from "../components/NavSidebar";
import PanZoomImage from "../components/PanZoomImage";
import { dbImageUrl } from "@sdai/api-client";
import FieldsPanel from "../components/FieldsPanel";
import LinksSection from "../components/LinksSection";
import AccessSection from "../components/AccessSection";
import SeriesSection from "../components/SeriesSection";

// The document-management record view: presents a document the way a real
// records system would — every extracted field as a labeled entry, no QA
// pipeline internals (confidence tier, review status, retry/delete). For
// the raw/technical view with that plumbing exposed, used by the ops data
// browser, see DataBrowserDetailPage.tsx at /ops/data/:tableName/:id.

// ── Types ─────────────────────────────────────────────────────────────────────

interface ListEntry { tableName: string; id: string; }
interface LocationState {
  list?:         ListEntry[];
  currentIndex?: number;
}

// Every system/pipeline column (mirrors documents_router._BASE_COLS, plus
// document_type/record_id/series_id which get their own dedicated spot in
// the header) — this page shows only the document's own extracted content,
// none of the QA/ingestion bookkeeping the ops data browser exposes.
const SKIP_FIELDS = new Set([
  "id", "source_image_path", "source_pdf_path", "page_image_paths",
  "batch_id", "batch_document_id", "ingested_at", "confidence",
  "review_status", "content_hash", "reviewed_at", "reviewed_by",
  "record_id", "series_id", "document_type", "uploaded_by",
]);

// ── Main component ────────────────────────────────────────────────────────────

export default function DocumentDetailPage() {
  const { tableName = "", id = "" } = useParams<{ tableName: string; id: string }>();
  const navigate  = useNavigate();
  const location  = useLocation();
  const state     = (location.state ?? {}) as LocationState;
  const list      = state.list ?? [];
  const idx       = state.currentIndex ?? -1;

  const { user }        = useAuth();
  const canEditExtraction = user?.role === "admin" || user?.can_edit_extraction || false;
  const { data, isLoading, error } = useDbDocumentDetail(tableName, id);

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
    navigate(`/documents/${entry.tableName}/${entry.id}`, {
      state: { list, currentIndex: newIdx },
    });
  }

  const imgSrc = pageImagePaths.length > 0
    ? dbImageUrl(pageImagePaths[Math.min(activePage, pageImagePaths.length - 1)])
    : data?.source_image_path
    ? dbImageUrl(String(data.source_image_path))
    : null;

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
          onClick={() => navigate("/documents")}
          className="text-gray-500 hover:text-gray-800 transition-colors"
          aria-label="Back to documents"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
        </button>

        <span className="text-sm font-semibold text-gray-700 truncate max-w-xs">
          {typeof data?.document_type === "string" && data.document_type
            ? data.document_type
            : tableName.replace(/_/g, " ")}
        </span>

        {typeof data?.record_id === "string" && data.record_id && (
          <span className="inline-flex items-center px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-xs font-mono">
            {data.record_id}
          </span>
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

          {/* Right — the record itself (45%) */}
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <h1 className="text-lg font-bold text-gray-900 mb-4">
              {typeof data?.document_type === "string" && data.document_type
                ? data.document_type
                : tableName.replace(/_/g, " ")}
            </h1>

            {tableName && id && (
              <FieldsPanel
                tableName={tableName}
                id={id}
                fieldRows={fieldRows}
                canEdit={canEditExtraction}
              />
            )}

            {tableName && id && <LinksSection tableName={tableName} id={id} />}
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
