import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { flagReview, patchReview, dbImageUrl } from "@sdai/api-client";
import { useReviewQueue } from "../hooks/useReview";
import { useDbDocumentDetail } from "../hooks/useDocumentsDb";
import NavSidebar from "../components/NavSidebar";
import type { ReviewQueueItem } from "@sdai/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const READONLY = new Set([
  "id", "table_name", "document_type", "confidence",
  "review_status", "source_image_path", "ingested_at",
  "reviewed_at", "batch_id",
]);

// ── Helpers ───────────────────────────────────────────────────────────────────

function tierColor(t: string | null) {
  if (t === "high")   return "bg-emerald-100 text-emerald-800";
  if (t === "medium") return "bg-amber-100 text-amber-800";
  if (t === "low")    return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-500";
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "unknown time";
  const ms = Date.now() - new Date(iso).getTime();
  const m  = Math.floor(ms / 60_000);
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function labelFor(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Pan/zoom image (same pattern as DocumentDetailPage) ───────────────────────

function PanZoomImage({ src }: { src: string }) {
  const [scale, setScale] = useState(1);
  const [tx, setTx]       = useState(0);
  const [ty, setTy]       = useState(0);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const reset = useCallback(() => { setScale(1); setTx(0); setTy(0); }, []);

  return (
    <div
      className="relative w-full h-full overflow-hidden bg-gray-800 select-none"
      onWheel={(e) => {
        e.preventDefault();
        setScale((s) => Math.min(8, Math.max(0.2, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
      }}
      onMouseDown={(e) => {
        if (e.button !== 0) return;
        drag.current = { x: e.clientX, y: e.clientY, tx, ty };
      }}
      onMouseMove={(e) => {
        if (!drag.current) return;
        setTx(drag.current.tx + e.clientX - drag.current.x);
        setTy(drag.current.ty + e.clientY - drag.current.y);
      }}
      onMouseUp={() => { drag.current = null; }}
      onMouseLeave={() => { drag.current = null; }}
      onDoubleClick={reset}
      style={{ cursor: drag.current ? "grabbing" : "grab" }}
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
      <div className="absolute bottom-2 right-2 bg-black/50 text-white text-[10px] px-2 py-0.5 rounded pointer-events-none">
        {Math.round(scale * 100)}% · scroll zoom · drag pan · dbl reset
      </div>
    </div>
  );
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function Toast({ message, visible }: { message: string; visible: boolean }) {
  return (
    <div
      className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-5 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-medium shadow-lg transition-all duration-300 ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2 pointer-events-none"
      }`}
    >
      {message}
    </div>
  );
}

// ── Editable form ─────────────────────────────────────────────────────────────

interface FormProps {
  detail:      Record<string, unknown>;
  form:        Record<string, string>;
  initial:     Record<string, string>;
  arrayFields: Set<string>;
  onChange:    (key: string, val: string) => void;
}

function ReviewForm({ detail, form, initial, arrayFields, onChange }: FormProps) {
  const confidence   = detail.confidence   ? String(detail.confidence)   : null;
  const documentType = detail.document_type ? String(detail.document_type) : null;

  const editableKeys = Object.keys(detail).filter((k) => !READONLY.has(k));

  return (
    <div className="flex flex-col h-full">
      {/* Read-only badges */}
      <div className="px-5 py-3 border-b border-gray-200 flex flex-wrap items-center gap-2 flex-shrink-0">
        {documentType && (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">
            {documentType}
          </span>
        )}
        {confidence && (
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold ${tierColor(confidence)}`}>
            {confidence} confidence
          </span>
        )}
      </div>

      {/* Editable fields */}
      <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
        {editableKeys.length === 0 && (
          <p className="text-sm text-gray-400">No editable fields.</p>
        )}
        {editableKeys.map((key) => {
          const val     = form[key] ?? "";
          const changed = val !== (initial[key] ?? "");
          const isArray = arrayFields.has(key);

          return (
            <div
              key={key}
              className={`pl-3 border-l-2 transition-colors ${
                changed ? "border-amber-400" : "border-transparent"
              }`}
            >
              <label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                {labelFor(key)}
                {isArray && (
                  <span className="ml-1 font-normal normal-case text-gray-400">(comma-separated)</span>
                )}
              </label>
              <input
                type="text"
                value={val}
                onChange={(e) => onChange(key, e.target.value)}
                placeholder="Not found — fill if visible"
                className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 placeholder-gray-300"
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ReviewPage() {
  const queryClient  = useQueryClient();

  // Load entire queue (up to 100) for navigation
  const { data: queueData, isLoading: queueLoading } = useReviewQueue({ page_size: 100 });

  // Local queue for optimistic removal after actions
  const [items, setItems]  = useState<ReviewQueueItem[]>([]);
  const [idx, setIdx]      = useState(0);

  useEffect(() => {
    if (queueData?.items) setItems(queueData.items);
  }, [queueData?.items]);

  const current    = items[idx] ?? null;
  const totalQueue = queueData?.total ?? items.length;

  // Load full detail for the current item
  const {
    data:      detail,
    isLoading: detailLoading,
  } = useDbDocumentDetail(current?.table_name ?? "", current?.id ?? "");

  // Form state
  const [form, setForm]               = useState<Record<string, string>>({});
  const [initial, setInitial]         = useState<Record<string, string>>({});
  const [arrayFields, setArrayFields] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!detail) return;
    const vals:   Record<string, string> = {};
    const arrays: Set<string>            = new Set();
    for (const [k, v] of Object.entries(detail)) {
      if (READONLY.has(k)) continue;
      if (Array.isArray(v)) {
        arrays.add(k);
        vals[k] = (v as unknown[]).map(String).join(", ");
      } else {
        vals[k] = v == null ? "" : String(v);
      }
    }
    setForm(vals);
    setInitial({ ...vals });
    setArrayFields(arrays);
  }, [detail]);

  // Toast
  const [toast, setToast] = useState({ message: "", visible: false });
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();

  function showToast(message: string) {
    setToast({ message, visible: true });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(
      () => setToast((t) => ({ ...t, visible: false })),
      2500,
    );
  }

  // After an action: remove current item, advance
  function advanceAfterAction() {
    setItems((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      setIdx((i) => Math.min(i, Math.max(0, next.length - 1)));
      return next;
    });
    queryClient.invalidateQueries({ queryKey: ["review-count"] });
    queryClient.invalidateQueries({ queryKey: ["review-queue"] });
  }

  // Build PATCH fields payload
  function buildFields(): Record<string, string | string[] | null> {
    const out: Record<string, string | string[] | null> = {};
    for (const [k, v] of Object.entries(form)) {
      if (arrayFields.has(k)) {
        out[k] = v.trim()
          ? v.split(",").map((s) => s.trim()).filter(Boolean)
          : [];
      } else {
        out[k] = v.trim() || null;
      }
    }
    return out;
  }

  // Mutations
  const approveMutation = useMutation({
    mutationFn: () =>
      patchReview(current!.table_name, current!.id, {
        fields: buildFields(),
        action: "approve",
      }),
    onSuccess: () => { showToast("Approved"); advanceAfterAction(); },
    onError:   (e: Error) => showToast(`Error: ${e.message}`),
  });

  const rejectMutation = useMutation({
    mutationFn: () =>
      patchReview(current!.table_name, current!.id, { fields: {}, action: "reject" }),
    onSuccess: () => { showToast("Rejected"); advanceAfterAction(); },
    onError:   (e: Error) => showToast(`Error: ${e.message}`),
  });

  const flagMutation = useMutation({
    mutationFn: () => flagReview(current!.table_name, current!.id),
    onSuccess:  () => { showToast("Flagged for manual entry"); advanceAfterAction(); },
    onError:    (e: Error) => showToast(`Error: ${e.message}`),
  });

  const isBusy =
    approveMutation.isPending || rejectMutation.isPending || flagMutation.isPending;

  // Navigation helpers
  const handleNext = useCallback(
    () => setIdx((i) => Math.min(i + 1, items.length - 1)),
    [items.length],
  );
  const handlePrev = useCallback(
    () => setIdx((i) => Math.max(i - 1, 0)),
    [],
  );
  const handleApprove = useCallback(() => {
    if (current && !isBusy) approveMutation.mutate();
  }, [current, isBusy, approveMutation]);
  const handleSkip = useCallback(() => handleNext(), [handleNext]);

  // Keyboard shortcuts (skip when focus is in an input)
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight") { e.preventDefault(); handleNext(); }
      if (e.key === "ArrowLeft")  { e.preventDefault(); handlePrev(); }
      if (e.key === "Enter")      { e.preventDefault(); handleApprove(); }
      if (e.key === "Escape")     { e.preventDefault(); handleSkip(); }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleNext, handlePrev, handleApprove, handleSkip]);

  // ── Type breakdown from loaded items ────────────────────────────────────────
  const typeCounts = items.reduce<Record<string, number>>((acc, item) => {
    const t = item.document_type ?? item.table_name;
    acc[t]  = (acc[t] ?? 0) + 1;
    return acc;
  }, {});

  const oldestIngested = items[0]?.ingested_at;
  const imgSrc = current?.source_image_path
    ? dbImageUrl(current.source_image_path)
    : null;

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-base font-bold text-indigo-700 tracking-tight">Review Queue</h1>

          {!queueLoading && (
            <div className="flex items-center gap-3 ml-2">
              <span className="text-2xl font-bold text-gray-800">{totalQueue}</span>
              <span className="text-xs text-gray-500">
                document{totalQueue !== 1 ? "s" : ""} waiting
                {oldestIngested && ` · oldest ${timeAgo(oldestIngested)}`}
              </span>
            </div>
          )}
        </div>

        {/* Type breakdown chips */}
        {Object.keys(typeCounts).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(typeCounts).map(([type, count]) => (
              <span
                key={type}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-indigo-50 text-indigo-700 border border-indigo-200"
              >
                <span className="font-medium">{count}</span>
                <span className="text-indigo-500">{type.replace(/_/g, " ")}</span>
              </span>
            ))}
          </div>
        )}
      </header>

      {/* ── Empty state ─────────────────────────────────────────────────────── */}
      {!queueLoading && items.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center">
            <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
            </svg>
          </div>
          <p className="text-sm text-gray-600 font-medium">Review queue is empty — all documents processed</p>
          <Link
            to="/upload"
            className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 transition-colors"
          >
            Upload more documents
          </Link>
        </div>
      ) : (
        <>
          {/* ── Navigation bar ──────────────────────────────────────────────── */}
          <div className="bg-white border-b border-gray-200 px-6 py-2 flex items-center gap-3 flex-shrink-0">
            <button
              onClick={handlePrev}
              disabled={idx <= 0 || isBusy}
              className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              ← Previous
            </button>
            <span className="text-xs text-gray-500 flex-1 text-center">
              {queueLoading ? "Loading…" : `Reviewing ${idx + 1} of ${totalQueue}`}
            </span>
            <button
              onClick={handleNext}
              disabled={idx >= items.length - 1 || isBusy}
              className="px-3 py-1.5 rounded-md text-xs border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              Next →
            </button>
          </div>

          {/* ── Two-panel layout ─────────────────────────────────────────────── */}
          <div className="flex flex-1 overflow-hidden">
            {/* Left 58% — image */}
            <div className="flex flex-col border-r border-gray-200" style={{ width: "58%" }}>
              <div className="flex-1 overflow-hidden">
                {imgSrc ? (
                  <PanZoomImage src={imgSrc} />
                ) : (
                  <div className="flex h-full items-center justify-center text-gray-400 text-sm bg-gray-100">
                    No image available
                  </div>
                )}
              </div>
              {/* Image metadata */}
              {current && (
                <div className="px-4 py-2 border-t border-gray-200 bg-white flex items-center gap-4 flex-shrink-0">
                  <span className="text-xs text-gray-500 truncate">
                    {current.source_image_path?.split("/").pop() ?? "—"}
                  </span>
                  {current.ingested_at && (
                    <span className="text-xs text-gray-400 ml-auto shrink-0">
                      Ingested {timeAgo(current.ingested_at)}
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* Right 42% — form + action bar */}
            <div className="flex flex-col overflow-hidden" style={{ width: "42%" }}>
              {detailLoading ? (
                <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
                  Loading fields…
                </div>
              ) : detail ? (
                <ReviewForm
                  detail={detail}
                  form={form}
                  initial={initial}
                  arrayFields={arrayFields}
                  onChange={(k, v) => setForm((f) => ({ ...f, [k]: v }))}
                />
              ) : (
                <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
                  Select a document
                </div>
              )}

              {/* ── Pinned action bar ──────────────────────────────────────── */}
              {current && (
                <div className="border-t border-gray-200 px-5 py-3 bg-white flex items-center gap-2 flex-shrink-0">
                  <button
                    onClick={() => approveMutation.mutate()}
                    disabled={isBusy || !detail}
                    className="flex-1 px-3 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-700 disabled:opacity-50 transition-colors"
                    title="Enter to approve"
                  >
                    {approveMutation.isPending ? "Approving…" : "Approve"}
                  </button>

                  <button
                    onClick={() => rejectMutation.mutate()}
                    disabled={isBusy || !detail}
                    className="flex-1 px-3 py-2 rounded-lg bg-red-600 text-white text-sm font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
                  >
                    {rejectMutation.isPending ? "Rejecting…" : "Reject"}
                  </button>

                  <button
                    onClick={() => flagMutation.mutate()}
                    disabled={isBusy || !detail}
                    className="px-3 py-2 rounded-lg border border-gray-300 text-gray-600 text-sm hover:bg-gray-50 disabled:opacity-50 transition-colors"
                    title="Flag for manual data entry"
                  >
                    {flagMutation.isPending ? "…" : "Flag"}
                  </button>

                  <button
                    onClick={handleSkip}
                    disabled={isBusy}
                    className="px-3 py-2 rounded-lg border border-gray-300 text-gray-500 text-sm hover:bg-gray-50 disabled:opacity-40 transition-colors"
                    title="Skip (Escape)"
                  >
                    Skip
                  </button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      <Toast message={toast.message} visible={toast.visible} />
      </div>
    </div>
  );
}
