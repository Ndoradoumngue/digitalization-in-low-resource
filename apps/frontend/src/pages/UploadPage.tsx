import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useUpload, useIngestPath, useBatchStatus } from "../hooks/useIngest";
import type { DocumentStatus } from "@sdai/types";

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<DocumentStatus, string> = {
  completed:        "bg-emerald-100 text-emerald-700",
  review_required:  "bg-amber-100   text-amber-700",
  crashed:          "bg-red-100     text-red-700",
  pending:          "bg-gray-100    text-gray-500",
  processing:       "bg-gray-100    text-gray-500",
  out_of_scope:     "bg-blue-100    text-blue-700",
};

const STATUS_LABELS: Record<DocumentStatus, string> = {
  completed:       "Completed",
  review_required: "Review",
  crashed:         "Crashed",
  pending:         "Pending",
  processing:      "Processing…",
  out_of_scope:    "Out of scope",
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

// ── Progress table ────────────────────────────────────────────────────────────

function ProgressTable({ batchId }: { batchId: string }) {
  const { data, isLoading } = useBatchStatus(batchId);

  if (isLoading || !data) {
    return <p className="text-sm text-gray-400 mt-6">Loading batch status…</p>;
  }

  const done = data.completed + data.crashed + data.review_required + data.out_of_scope;

  return (
    <div className="mt-8 space-y-4">
      {/* Summary bar */}
      <div className="flex items-center gap-6 text-sm">
        <span className="font-medium text-gray-700">
          {done} / {data.total} processed
        </span>
        <span className="text-emerald-600">✓ {data.completed} approved</span>
        <span className="text-amber-600">⚑ {data.review_required} review</span>
        <span className="text-red-500">✕ {data.crashed} crashed</span>
        <span className="text-blue-500">◌ {data.out_of_scope} out of scope</span>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 transition-all duration-700"
          style={{ width: `${data.total ? (done / data.total) * 100 : 0}%` }}
        />
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
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 bg-white">
            {data.documents.map((doc) => (
              <tr key={doc.id} className="hover:bg-gray-50/60 transition-colors">
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Upload page ───────────────────────────────────────────────────────────────

type Mode = "files" | "path" | "drive";

export default function UploadPage() {
  const navigate      = useNavigate();
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
    <div className="min-h-screen bg-gray-50 font-sans">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4">
        <button
          onClick={() => navigate("/")}
          className="text-sm text-gray-500 hover:text-indigo-600 transition-colors"
        >
          ← Dashboard
        </button>
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
      </div>
    </div>
  );
}
