import { useTranslation } from "react-i18next";
import { exportJobDownloadUrl } from "@sdai/api-client";
import type { ExportJob } from "@sdai/types";
import NavSidebar from "../components/NavSidebar";
import { useConfirm } from "../context/ConfirmContext";
import { useDeleteExportJob, useDownloadFile, useExportJobs } from "../hooks/useSharing";

// The signed-in person's selective exports: a ZIP of originals + metadata +
// sha256 manifest, built in the background and kept 24 h.

const STATUS_STYLE: Record<ExportJob["status"], string> = {
  queued: "bg-gray-100 text-gray-600",
  running: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-700",
  expired: "bg-gray-100 text-gray-500",
  deleted: "bg-gray-100 text-gray-500",
};

function formatSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ExportsPage() {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const { data, isLoading, error } = useExportJobs();
  const download = useDownloadFile();
  const remove = useDeleteExportJob();
  const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString(i18n.language) : "");

  async function onDelete(job: ExportJob) {
    if (!(await confirm({ message: t("exports.deleteConfirm"), danger: true, confirmLabel: t("exports.delete") }))) return;
    remove.mutate(job.id);
  }

  const jobs = data?.jobs ?? [];

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0">
          <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("exports.header")}</h1>
          <p className="text-xs text-gray-500 mt-0.5">{t("exports.intro")}</p>
        </header>
        <div className="flex-1 overflow-auto p-6">
          {error ? (
            <p className="text-sm text-red-500">{t("exports.loadError")}</p>
          ) : isLoading ? (
            <p className="text-sm text-gray-400">{t("exports.loading")}</p>
          ) : jobs.length === 0 ? (
            <p className="text-sm text-gray-400">{t("exports.none")}</p>
          ) : (
            <ul className="max-w-3xl space-y-3">
              {jobs.map((job) => (
                <li key={job.id} className="bg-white rounded-lg border border-gray-200 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-semibold ${STATUS_STYLE[job.status]}`}>
                      {t(`exports.status.${job.status}`)}
                    </span>
                    <span className="text-sm font-medium text-gray-800">
                      {t(`exports.kind.${String(job.selection?.kind ?? "documents")}`)}
                      {" · "}
                      {t("exports.documents", { count: job.document_count ?? 0 })}
                      {" · "}
                      {t(`exports.versions.${job.versions}`)}
                    </span>
                    <span className="ml-auto text-xs text-gray-400">{fmt(job.created_at)}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
                    <span>{t("exports.files", { count: job.file_count ?? 0 })}</span>
                    {job.total_bytes != null && <span>{formatSize(job.total_bytes)}</span>}
                    {job.status === "done" && job.expires_at && (
                      <span>{t("exports.availableUntil", { date: fmt(job.expires_at) })}</span>
                    )}
                    {job.zip_sha256 && (
                      <span className="font-mono" title={job.zip_sha256}>sha256 {job.zip_sha256.slice(0, 12)}…</span>
                    )}
                    {job.error && <span className="text-red-600">{t("exports.failedReason", { reason: job.error })}</span>}
                  </div>
                  {job.warnings && job.warnings.length > 0 && (
                    <p className="mt-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                      {t("exports.warnings", { count: job.warnings.length })}
                    </p>
                  )}
                  {(job.status === "done" || job.status === "failed") && (
                    <div className="mt-3 flex gap-3">
                      {job.status === "done" && (
                        <button
                          onClick={() => download.mutate(exportJobDownloadUrl(job.id))}
                          disabled={download.isPending}
                          className="px-3 py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                        >
                          {t("exports.download")}
                        </button>
                      )}
                      <button
                        onClick={() => onDelete(job)}
                        disabled={remove.isPending}
                        className="px-3 py-1.5 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                      >
                        {t("exports.delete")}
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {download.isError && (
            <p role="alert" className="mt-3 text-xs text-red-600">{(download.error as Error).message}</p>
          )}
        </div>
      </div>
    </div>
  );
}
