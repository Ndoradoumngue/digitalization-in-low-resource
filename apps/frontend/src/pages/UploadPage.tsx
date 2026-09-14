import { Fragment, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useUpload,
  useIngestPath,
  useBatchStatus,
  useRecentBatches,
  usePageStatus,
  useRetryPage,
  useReloadPage,
  useManualEnterPage,
  useSkipPage,
  useResumeDocument,
  useDeleteIngestedDocument,
} from "../hooks/useIngest";
import NavSidebar from "../components/NavSidebar";
import { useAuth } from "../context/AuthContext";
import { dbImageUrl } from "@sdai/api-client";
import type { DocumentStatus, BatchSummary, Page } from "@sdai/types";

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<DocumentStatus, string> = {
  completed:        "bg-emerald-100 text-emerald-700",
  review_required:  "bg-amber-100   text-amber-700",
  crashed:          "bg-red-100     text-red-700",
  pending:          "bg-gray-100    text-gray-500",
  processing:       "bg-gray-100    text-gray-500",
  out_of_scope:     "bg-blue-100    text-blue-700",
  duplicate:        "bg-violet-100  text-violet-700",
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  completed:       "Completed",
  review_required: "Review",
  crashed:         "Crashed",
  pending:         "Pending",
  processing:      "Processing…",
  out_of_scope:    "Out of scope",
  duplicate:       "Duplicate",
};

function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

// ── Per-page progress (multi-page documents) ─────────────────────────────────

function ManualEntryForm({
  pageId,
  batchDocumentId,
  onDone,
}: {
  pageId: string;
  batchDocumentId: string;
  onDone: () => void;
}) {
  const [json, setJson]   = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const manualMutation    = useManualEnterPage(batchDocumentId);

  function submit() {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(json);
    } catch {
      setError("Invalid JSON");
      return;
    }
    setError(null);
    manualMutation.mutate(
      { pageId, fields: parsed },
      { onSuccess: onDone, onError: (e: Error) => setError(e.message) },
    );
  }

  return (
    <div className="mt-2 space-y-1.5">
      <textarea
        value={json}
        onChange={(e) => setJson(e.target.value)}
        rows={4}
        className="w-full font-mono text-xs rounded-md border border-gray-300 px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        placeholder='{"entries": [{"kabalay": "...", "french": "..."}]}'
      />
      {error && <p className="text-xs text-red-500">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={manualMutation.isPending}
          className="px-2.5 py-1 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {manualMutation.isPending ? "Saving…" : "Save"}
        </button>
        <button
          onClick={onDone}
          className="px-2.5 py-1 rounded-md text-xs text-gray-500 hover:bg-gray-100 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function ViewImageLink({ imagePath }: { imagePath: string }) {
  return (
    <a
      href={dbImageUrl(imagePath)}
      target="_blank"
      rel="noreferrer"
      className="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors flex-shrink-0"
    >
      View page ↗
    </a>
  );
}

function SkipButton({ pageId, batchDocumentId }: { pageId: string; batchDocumentId: string }) {
  const skipMutation = useSkipPage(batchDocumentId);
  return (
    <button
      onClick={() => skipMutation.mutate(pageId)}
      disabled={skipMutation.isPending}
      title="Mark as not relevant (cover page, blank, out of scope) — excluded from the extracted result"
      className="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-colors"
    >
      {skipMutation.isPending ? "Skipping…" : "Skip"}
    </button>
  );
}

function ReloadButton({ pageId, batchDocumentId }: { pageId: string; batchDocumentId: string }) {
  const reloadMutation = useReloadPage(batchDocumentId);
  return (
    <>
      <button
        onClick={() => reloadMutation.mutate(pageId)}
        disabled={reloadMutation.isPending}
        title="Re-derive this page from the original document from scratch (re-runs page extraction and layout detection, e.g. column splitting) — unlike Retry/Re-run, which reuses the already-processed image"
        className="px-2.5 py-1 rounded-md text-xs font-medium border border-violet-300 text-violet-700 hover:bg-violet-50 disabled:opacity-50 transition-colors flex-shrink-0"
      >
        {reloadMutation.isPending ? "Reloading…" : "Reload from source"}
      </button>
      {reloadMutation.isError && (
        // basis-full forces this onto its own line within the flex-wrap
        // row instead of stretching the row horizontally off-screen.
        <span className="basis-full text-xs text-red-500 break-words">
          {(reloadMutation.error as Error).message}
        </span>
      )}
    </>
  );
}

function FailedPageRow({ page, batchDocumentId }: { page: Page; batchDocumentId: string }) {
  const [manualOpen, setManualOpen] = useState(false);
  const [reasonOpen, setReasonOpen] = useState(false);
  const retryMutation = useRetryPage(batchDocumentId);

  return (
    <div className="rounded-lg border border-red-200 bg-red-50/60 px-3 py-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <span className="text-xs font-semibold text-red-700">Page {page.page_number}</span>
          {/* A native <button> has notoriously inconsistent white-space/
              wrapping behavior for its text content across browsers, even
              with an explicit override — use a clickable div instead so
              the expanded reason actually wraps. */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => setReasonOpen((o) => !o)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setReasonOpen((o) => !o); }
            }}
            title="Click to see the full reason"
            className={`cursor-pointer text-xs text-red-600 hover:text-red-800 hover:underline w-full ${
              reasonOpen ? "whitespace-pre-wrap break-words" : "truncate"
            }`}
          >
            {page.error_message ?? "Failed"}
          </div>
        </div>
        <div className="flex items-center flex-wrap gap-2 flex-shrink-0">
          <ViewImageLink imagePath={page.image_path} />
          <button
            onClick={() => retryMutation.mutate(page.id)}
            disabled={retryMutation.isPending}
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
          >
            {retryMutation.isPending ? "Retrying…" : "Retry"}
          </button>
          <ReloadButton pageId={page.id} batchDocumentId={batchDocumentId} />
          <button
            onClick={() => setManualOpen((o) => !o)}
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {manualOpen ? "Cancel" : "Enter manually"}
          </button>
          <SkipButton pageId={page.id} batchDocumentId={batchDocumentId} />
        </div>
      </div>
      {retryMutation.isError && (
        <p className="text-xs text-red-500 mt-1">{(retryMutation.error as Error).message}</p>
      )}
      {manualOpen && (
        <ManualEntryForm
          pageId={page.id}
          batchDocumentId={batchDocumentId}
          onDone={() => setManualOpen(false)}
        />
      )}
    </div>
  );
}

function CompletedPageRow({ page, batchDocumentId }: { page: Page; batchDocumentId: string }) {
  const [open, setOpen] = useState(false);
  const retryMutation = useRetryPage(batchDocumentId);
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-semibold text-gray-700">
          Page {page.page_number}
          {page.status === "manual" && (
            <span className="ml-1.5 text-[10px] font-medium text-violet-600">(manual)</span>
          )}
        </span>
        <div className="flex items-center flex-wrap gap-2 flex-shrink-0">
          <ViewImageLink imagePath={page.image_path} />
          <button
            onClick={() => setOpen((o) => !o)}
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            {open ? "Hide data" : "View data"}
          </button>
          <button
            onClick={() => retryMutation.mutate(page.id)}
            disabled={retryMutation.isPending}
            title="Re-run extraction on this page's stored image (e.g. to pick up a prompt change) and merge the new result in, replacing what's there now. Uses the same stored image — won't re-evaluate page layout/splitting."
            className="px-2.5 py-1 rounded-md text-xs font-medium border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
          >
            {retryMutation.isPending ? "Re-running…" : "Re-run"}
          </button>
          <ReloadButton pageId={page.id} batchDocumentId={batchDocumentId} />
          <SkipButton pageId={page.id} batchDocumentId={batchDocumentId} />
        </div>
      </div>
      {retryMutation.isError && (
        <p className="text-xs text-red-500 mt-1">{(retryMutation.error as Error).message}</p>
      )}
      {open && (
        <pre className="mt-2 text-xs font-mono bg-gray-50 rounded-md p-2 overflow-x-auto whitespace-pre-wrap break-words">
          {JSON.stringify(page.fields, null, 2)}
        </pre>
      )}
    </div>
  );
}

function SkippedPageRow({ page }: { page: Page }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-1.5 rounded-lg border border-gray-100 bg-gray-50">
      <span className="text-xs text-gray-400">Page {page.page_number} — skipped (not relevant)</span>
      <ViewImageLink imagePath={page.image_path} />
    </div>
  );
}

function PendingPageRow({ page, batchDocumentId }: { page: Page; batchDocumentId: string }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-1.5">
      <span className="flex items-center gap-2 text-xs text-gray-400">
        <span
          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
            page.status === "processing" ? "bg-indigo-400 animate-pulse" : "bg-gray-300"
          }`}
        />
        Page {page.page_number} — {page.status === "processing" ? "processing…" : "pending"}
      </span>
      {page.status === "pending" && (
        <div className="flex items-center flex-wrap gap-2 flex-shrink-0">
          <ViewImageLink imagePath={page.image_path} />
          <SkipButton pageId={page.id} batchDocumentId={batchDocumentId} />
        </div>
      )}
    </div>
  );
}

function DocumentPageProgress({ batchDocumentId }: { batchDocumentId: string }) {
  const { data } = usePageStatus(batchDocumentId);
  const resumeMutation = useResumeDocument();

  // Only worth showing for genuinely multi-page documents.
  if (!data || data.total <= 1) return null;

  const done = data.completed + data.failed + data.manual + data.skipped;
  const sortedPages = [...data.pages].sort((a, b) => a.page_number - b.page_number);
  const canResume = data.pending > 0;

  return (
    <div className="px-4 py-3 bg-gray-50/70 space-y-2">
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>
          Pages: {done} / {data.total} processed
          {data.failed > 0 ? ` · ${data.failed} failed` : ""}
          {data.skipped > 0 ? ` · ${data.skipped} skipped` : ""}
        </span>
        <div className="flex items-center gap-2">
          {canResume && (
            <button
              onClick={() => resumeMutation.mutate(batchDocumentId)}
              disabled={resumeMutation.isPending}
              title="Process every page that hasn't been attempted yet — use this if the run was interrupted (e.g. a server restart) and hasn't moved in a while"
              className="px-2 py-0.5 rounded-md text-xs font-medium border border-indigo-300 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50 transition-colors"
            >
              {resumeMutation.isPending ? "Resuming…" : `Resume remaining (${data.pending})`}
            </button>
          )}
          <span>{data.total ? Math.round((done / data.total) * 100) : 0}%</span>
        </div>
      </div>
      {resumeMutation.isError && (
        <p className="text-xs text-red-500">{(resumeMutation.error as Error).message}</p>
      )}
      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-400 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${data.total ? (done / data.total) * 100 : 0}%` }}
        />
      </div>
      <div className="space-y-1.5 pt-1 max-h-96 overflow-y-auto">
        {sortedPages.map((p) => {
          if (p.status === "failed") {
            return <FailedPageRow key={p.id} page={p} batchDocumentId={batchDocumentId} />;
          }
          if (p.status === "completed" || p.status === "manual") {
            return <CompletedPageRow key={p.id} page={p} batchDocumentId={batchDocumentId} />;
          }
          if (p.status === "skipped") {
            return <SkippedPageRow key={p.id} page={p} />;
          }
          return <PendingPageRow key={p.id} page={p} batchDocumentId={batchDocumentId} />;
        })}
      </div>
    </div>
  );
}

function DeleteDocumentButton({ batchDocumentId }: { batchDocumentId: string }) {
  const { user } = useAuth();
  const deleteMutation = useDeleteIngestedDocument();

  if (user?.role !== "admin") return null;

  return (
    <div className="inline-flex flex-col items-end gap-1">
      <button
        onClick={() => {
          if (window.confirm(
            "Delete the extracted document for this upload? This lets you re-upload " +
            "the same file without it being blocked as a duplicate. This cannot be undone."
          )) {
            deleteMutation.mutate(batchDocumentId);
          }
        }}
        disabled={deleteMutation.isPending || deleteMutation.isSuccess}
        title="Delete so this file can be re-uploaded without hitting the duplicate check"
        className="px-2.5 py-1 rounded-md text-xs font-medium border border-red-300 text-red-700 hover:bg-red-50 disabled:opacity-50 transition-colors"
      >
        {deleteMutation.isPending ? "Deleting…" : deleteMutation.isSuccess ? "Deleted" : "Delete"}
      </button>
      {deleteMutation.isError && (
        <span className="text-xs text-red-500">{(deleteMutation.error as Error).message}</span>
      )}
    </div>
  );
}

// ── Progress table ────────────────────────────────────────────────────────────

function ProgressTable({ batchId }: { batchId: string }) {
  const { data, isLoading, refetch, isFetching } = useBatchStatus(batchId);
  const queryClient = useQueryClient();

  if (isLoading || !data) {
    return <p className="text-sm text-gray-400 mt-6">Loading batch status…</p>;
  }

  const done = data.completed + data.crashed + data.review_required + data.out_of_scope + data.duplicates_skipped;

  function refreshAll() {
    refetch();
    // ProgressTable doesn't own the per-document page-status queries
    // (DocumentPageProgress does), so invalidate them by key prefix too.
    queryClient.invalidateQueries({ queryKey: ["ingest-pages"] });
  }

  return (
    <div className="mt-8 space-y-4">
      {/* Summary bar */}
      <div className="flex items-center gap-6 text-sm flex-wrap">
        <span className="font-medium text-gray-700">
          {done} / {data.total} processed
        </span>
        <button
          onClick={refreshAll}
          disabled={isFetching}
          className="ml-auto px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          {isFetching ? "Refreshing…" : "↻ Refresh"}
        </button>
        <span className="text-emerald-600">✓ {data.completed} approved</span>
        <span className="text-amber-600">⚑ {data.review_required} review</span>
        <span className="text-red-500">✕ {data.crashed} crashed</span>
        <span className="text-blue-500">◌ {data.out_of_scope} out of scope</span>
        {data.duplicates_skipped > 0 && (
          <span className="text-violet-600 inline-flex items-center gap-1">
            ⊜ {data.duplicates_skipped} duplicate
            <span
              className="cursor-help text-violet-400"
              title="These files were already ingested. Their content hash matched an existing document so processing was skipped."
            >
              ⓘ
            </span>
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex items-center justify-between text-xs font-medium text-gray-500 mb-1.5">
          <span>Processing progress</span>
          <span>{data.total ? Math.round((done / data.total) * 100) : 0}%</span>
        </div>
        <div className="h-3 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-500 rounded-full transition-all duration-700 ease-out"
            style={{ width: `${data.total ? (done / data.total) * 100 : 0}%` }}
          />
        </div>
      </div>

      {/* Document table */}
      <div className="overflow-x-auto rounded-xl border border-gray-200">
        <table className="min-w-full text-sm divide-y divide-gray-100">
          <thead className="bg-gray-50 text-xs font-semibold text-gray-500 uppercase tracking-wide">
            <tr>
              <th className="px-4 py-3 text-left">Filename</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-left">Document type</th>
              <th className="px-4 py-3 text-left">Confidence</th>
              <th className="px-4 py-3 text-right">Time (s)</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 bg-white">
            {data.documents.map((doc) => (
              <Fragment key={doc.id}>
                <tr className="hover:bg-gray-50/60 transition-colors">
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-700 max-w-xs truncate">
                    {doc.filename}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={doc.status} />
                  </td>
                  <td className="px-4 py-2.5 text-gray-600">
                    {doc.document_type ?? <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-gray-600 capitalize">
                    {doc.confidence ?? <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-500">
                    {doc.processing_time != null
                      ? doc.processing_time.toFixed(1)
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {["completed", "review_required", "crashed"].includes(doc.status) && (
                      <DeleteDocumentButton batchDocumentId={doc.id} />
                    )}
                  </td>
                </tr>
                <tr>
                  {/* width: 0 keeps this cell's expandable content (which can
                      contain long unbroken text like a JSON dump) from
                      inflating the table's auto layout — it wraps to
                      whatever width the other, normal rows establish
                      instead of growing the whole table horizontally. */}
                  <td colSpan={6} className="p-0" style={{ width: 0 }}>
                    <DocumentPageProgress batchDocumentId={doc.id} />
                  </td>
                </tr>
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Recent uploads ────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60)   return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function RecentBatches({ onSelect }: { onSelect: (batchId: string) => void }) {
  const { data, isLoading } = useRecentBatches();

  if (isLoading || !data || data.length === 0) return null;

  return (
    <div className="mt-10 pt-8 border-t border-gray-200">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Recent uploads</h2>
      <div className="space-y-1.5">
        {data.map((b: BatchSummary) => {
          const done = b.completed + b.crashed + b.review_required + b.out_of_scope + b.duplicates_skipped;
          const inProgress = b.pending > 0;
          return (
            <button
              key={b.batch_id}
              onClick={() => onSelect(b.batch_id)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg border border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50/40 text-left transition-colors"
            >
              {inProgress ? (
                <span className="flex-shrink-0 w-2 h-2 rounded-full bg-indigo-500 animate-pulse" title="In progress" />
              ) : (
                <span className="flex-shrink-0 w-2 h-2 rounded-full bg-gray-300" title="Finished" />
              )}
              <span className="text-xs font-mono text-gray-500 flex-shrink-0">
                {b.batch_id.slice(0, 8)}
              </span>
              <span className="text-xs text-gray-400 flex-shrink-0 capitalize">
                {b.source_type.replace(/_/g, " ")}
              </span>
              <span className="text-xs text-gray-600 flex-1">
                {inProgress ? `${done} / ${b.total} processed` : `${b.total} document${b.total === 1 ? "" : "s"}`}
              </span>
              {b.crashed > 0 && (
                <span className="text-xs text-red-500 flex-shrink-0">{b.crashed} crashed</span>
              )}
              <span className="text-xs text-gray-400 flex-shrink-0">{timeAgo(b.created_at)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Upload page ───────────────────────────────────────────────────────────────

type Mode = "files" | "path" | "drive";

export default function UploadPage() {
  const [mode, setMode]       = useState<Mode>("files");
  const [files, setFiles]     = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [serverPath, setServerPath] = useState("");
  const [driveId, setDriveId]   = useState("");
  const [batchId, setBatchId]   = useState<string | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const upload     = useUpload();
  const ingestPath = useIngestPath();

  // ── Drag-and-drop handlers ────────────────────────────────────────────────

  function onDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragging(true);
  }

  function onDragLeave() {
    setDragging(false);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      /\.(jpe?g|png|pdf)$/i.test(f.name)
    );
    setFiles((prev) => [...prev, ...dropped]);
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(e.target.files ?? []);
    setFiles((prev) => [...prev, ...selected]);
    e.target.value = "";
  }

  function removeFile(name: string) {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  }

  // ── Submit ────────────────────────────────────────────────────────────────

  async function handleSubmit() {
    setError(null);
    try {
      if (mode === "files") {
        if (files.length === 0) { setError("Select at least one file."); return; }
        const { batch_id } = await upload.mutateAsync(files);
        setBatchId(batch_id);
      } else if (mode === "path") {
        if (!serverPath.trim()) { setError("Enter a server directory path."); return; }
        const { batch_id } = await ingestPath.mutateAsync({ path: serverPath.trim() });
        setBatchId(batch_id);
      } else {
        if (!driveId.trim()) { setError("Enter a Google Drive folder ID."); return; }
        const { batch_id } = await ingestPath.mutateAsync({
          google_drive_folder_id: driveId.trim(),
        });
        setBatchId(batch_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
  }

  const isPending = upload.isPending || ingestPath.isPending;

  return (
    <div className="flex h-screen bg-gray-50 font-sans overflow-hidden">
      <NavSidebar />
      <div className="flex-1 overflow-y-auto">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4">
        <h1 className="text-base font-semibold text-gray-900">Upload documents</h1>
      </header>

      <div className="max-w-3xl mx-auto px-6 py-10">
        {!batchId ? (
          <>
            {/* Mode tabs */}
            <div className="flex gap-1 mb-6 bg-gray-100 rounded-lg p-1 w-fit">
              {(["files", "path", "drive"] as Mode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                    mode === m
                      ? "bg-white text-indigo-700 shadow-sm"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {m === "files" ? "File upload" : m === "path" ? "Server path" : "Google Drive"}
                </button>
              ))}
            </div>

            {/* ── File upload mode ── */}
            {mode === "files" && (
              <div className="space-y-4">
                <div
                  onDragOver={onDragOver}
                  onDragLeave={onDragLeave}
                  onDrop={onDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className={`flex flex-col items-center justify-center h-52 border-2 border-dashed rounded-xl cursor-pointer transition-colors ${
                    dragging
                      ? "border-indigo-400 bg-indigo-50"
                      : "border-gray-300 hover:border-indigo-300 hover:bg-gray-50"
                  }`}
                >
                  <svg
                    className="w-10 h-10 text-gray-300 mb-3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
                    />
                  </svg>
                  <p className="text-sm text-gray-500">
                    Drag &amp; drop JPG, PNG, or PDF files here
                  </p>
                  <p className="text-xs text-gray-400 mt-1">or click to browse</p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".jpg,.jpeg,.png,.pdf"
                    className="hidden"
                    onChange={onFileChange}
                  />
                </div>

                {files.length > 0 && (
                  <ul className="space-y-1 max-h-48 overflow-y-auto">
                    {files.map((f) => (
                      <li
                        key={f.name}
                        className="flex items-center justify-between px-3 py-1.5 bg-white rounded-lg border border-gray-100 text-sm"
                      >
                        <span className="truncate text-gray-700 font-mono text-xs">
                          {f.name}
                        </span>
                        <button
                          onClick={() => removeFile(f.name)}
                          className="ml-3 text-gray-300 hover:text-red-400 flex-shrink-0"
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {/* ── Server path mode ── */}
            {mode === "path" && (
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-700">
                  Server directory path
                </label>
                <input
                  type="text"
                  value={serverPath}
                  onChange={(e) => setServerPath(e.target.value)}
                  placeholder="/data/incoming/batch_jan2025"
                  className="w-full rounded-lg border border-gray-300 px-3.5 py-2.5 text-sm font-mono text-gray-900 placeholder-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <p className="text-xs text-gray-400">
                  Must be accessible on the API server filesystem.
                </p>
              </div>
            )}

            {/* ── Google Drive mode ── */}
            {mode === "drive" && (
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-700">
                  Google Drive folder ID
                </label>
                <input
                  type="text"
                  value={driveId}
                  onChange={(e) => setDriveId(e.target.value)}
                  placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
                  className="w-full rounded-lg border border-gray-300 px-3.5 py-2.5 text-sm font-mono text-gray-900 placeholder-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <p className="text-xs text-gray-400">
                  Found in the folder URL after{" "}
                  <span className="font-mono">drive.google.com/drive/folders/</span>.
                  Requires a service account with read access.
                </p>
              </div>
            )}

            {error && (
              <div className="mt-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={isPending}
              className="mt-6 w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {isPending ? "Starting…" : "Start ingestion"}
            </button>
          </>
        ) : (
          <>
            <div className="flex items-center justify-between mb-2">
              <div>
                <h2 className="text-base font-semibold text-gray-900">Ingestion started</h2>
                <p className="text-xs text-gray-400 font-mono mt-0.5">
                  batch {batchId}
                </p>
              </div>
              <button
                onClick={() => { setBatchId(null); setFiles([]); setServerPath(""); setDriveId(""); }}
                className="text-xs text-gray-400 hover:text-indigo-600 transition-colors"
              >
                New batch
              </button>
            </div>
            <ProgressTable batchId={batchId} />
          </>
        )}

        <RecentBatches onSelect={setBatchId} />
      </div>
      </div>
    </div>
  );
}
