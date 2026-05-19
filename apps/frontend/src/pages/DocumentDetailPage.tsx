import { useRef, useState, useEffect, useCallback } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useDbDocumentDetail } from "../hooks/useDocumentsDb";
import { dbImageUrl } from "@sdai/api-client";

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

const SKIP_FIELDS = new Set(["id", "source_image_path"]);

function labelFor(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Pan/zoom image viewer ─────────────────────────────────────────────────────

function PanZoomImage({ src }: { src: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale]   = useState(1);
  const [tx, setTx]         = useState(0);
  const [ty, setTy]         = useState(0);
  const dragRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);

  const reset = useCallback(() => { setScale(1); setTx(0); setTy(0); }, []);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => Math.min(8, Math.max(0.25, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
  }, []);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    dragRef.current = { startX: e.clientX, startY: e.clientY, tx, ty };
  }, [tx, ty]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current) return;
    const { startX, startY, tx: ox, ty: oy } = dragRef.current;
    setTx(ox + e.clientX - startX);
    setTy(oy + e.clientY - startY);
  }, []);

  const onMouseUp = useCallback(() => { dragRef.current = null; }, []);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-gray-800 select-none"
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
      onDoubleClick={reset}
      style={{ cursor: dragRef.current ? "grabbing" : "grab" }}
    >
      <img
        src={src}
        alt="Document"
        draggable={false}
        className="absolute top-1/2 left-1/2 max-w-none"
        style={{
          transform: `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale})`,
          transformOrigin: "center center",
        }}
      />
      <div className="absolute bottom-3 right-3 bg-black/50 text-white text-xs px-2 py-1 rounded pointer-events-none">
        {Math.round(scale * 100)}% — scroll to zoom · drag to pan · dbl-click to reset
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DocumentDetailPage() {
  const { tableName = "", id = "" } = useParams<{ tableName: string; id: string }>();
  const navigate  = useNavigate();
  const location  = useLocation();
  const state     = (location.state ?? {}) as LocationState;
  const list      = state.list ?? [];
  const idx       = state.currentIndex ?? -1;

  const { data, isLoading, error } = useDbDocumentDetail(tableName, id);

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

  const imgSrc = data?.source_image_path
    ? dbImageUrl(String(data.source_image_path))
    : null;

  const confidence   = data?.confidence   ? String(data.confidence)   : null;
  const reviewStatus = data?.review_status ? String(data.review_status) : null;

  // Build display rows — skip internal fields
  const fieldRows = data
    ? Object.entries(data).filter(([k]) => !SKIP_FIELDS.has(k))
    : [];

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans text-gray-900">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-3 flex-shrink-0">
        <button
          onClick={() => navigate("/documents")}
          className="text-gray-500 hover:text-gray-800 transition-colors"
          aria-label="Back to list"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
        </button>

        <span className="text-sm font-semibold text-gray-700 truncate max-w-xs">
          {tableName.replace(/_/g, " ")}
        </span>

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
          <div className="w-[55%] flex-shrink-0 border-r border-gray-200">
            {imgSrc ? (
              <PanZoomImage src={imgSrc} />
            ) : (
              <div className="flex h-full items-center justify-center text-gray-400 text-sm bg-gray-100">
                No image available
              </div>
            )}
          </div>

          {/* Right — extracted fields (45%) */}
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-1">
            {fieldRows.length === 0 ? (
              <p className="text-sm text-gray-400">No fields available.</p>
            ) : (
              fieldRows.map(([key, value]) => {
                const strVal = value === null || value === undefined ? null : String(value);
                return (
                  <div key={key} className="py-2 border-b border-gray-100 last:border-0">
                    <dt className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                      {labelFor(key)}
                    </dt>
                    <dd className="text-sm text-gray-800 break-words">
                      {strVal ?? <span className="text-gray-400 italic">—</span>}
                    </dd>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
