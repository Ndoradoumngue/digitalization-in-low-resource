import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { documentVersionFileUrl } from "@sdai/api-client";
import type { DocumentVersion, VersionChangeResponse } from "@sdai/types";
import { useConfirm } from "../context/ConfirmContext";
import { useDocumentVersions, useRestoreVersion, useUploadVersion } from "../hooks/useVersions";

// Every file this document has had. Nothing here rewrites history: a new
// upload becomes version N+1, and restoring an old version adds yet another
// version pointing at that old file (the backend refuses anything else).

function formatSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function VersionRow({
  version,
  tableName,
  id,
  canDownload,
  canRestore,
  onRestore,
  restoring,
}: {
  version: DocumentVersion;
  tableName: string;
  id: string;
  canDownload: boolean;
  canRestore: boolean;
  onRestore: () => void;
  restoring: boolean;
}) {
  const { t, i18n } = useTranslation();
  const v = version;
  return (
    <div className="py-2.5 border-b border-gray-100 last:border-0">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-gray-100 text-gray-700 font-mono">
          v{v.version_no}
        </span>
        {v.is_current && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-emerald-100 text-emerald-800">
            {t("versions.current")}
          </span>
        )}
        <span className="flex-1 min-w-0 text-sm text-gray-800 truncate" title={v.original_filename ?? ""}>
          {v.original_filename ?? t("versions.unnamed")}
        </span>
        {canDownload && (
          <>
            <a
              href={documentVersionFileUrl(tableName, id, v.version_no, true)}
              target="_blank"
              rel="noreferrer"
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              {t("versions.open")}
            </a>
            <a
              href={documentVersionFileUrl(tableName, id, v.version_no)}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              {t("versions.download")}
            </a>
          </>
        )}
        {canRestore && !v.is_current && (
          <button
            onClick={onRestore}
            disabled={restoring}
            className="text-xs font-medium text-amber-700 hover:text-amber-900 disabled:opacity-40"
          >
            {t("versions.restore")}
          </button>
        )}
      </div>
      <div className="mt-1 text-xs text-gray-500 flex flex-wrap gap-x-3">
        {v.created_at && <span>{new Date(v.created_at).toLocaleString(i18n.language)}</span>}
        {v.created_by_email && <span>{v.created_by_email}</span>}
        {v.size != null && <span>{formatSize(v.size)}</span>}
        {v.restored_from != null && (
          <span className="text-amber-700">{t("versions.restoredFrom", { n: v.restored_from })}</span>
        )}
        {v.file_hash && (
          <span className="font-mono" title={v.file_hash}>
            sha256 {v.file_hash.slice(0, 12)}…
          </span>
        )}
      </div>
      {v.comment && <p className="mt-1 text-xs text-gray-700 italic">“{v.comment}”</p>}
    </div>
  );
}

function UploadForm({
  tableName,
  id,
  onDone,
}: {
  tableName: string;
  id: string;
  onDone: (result?: VersionChangeResponse) => void;
}) {
  const { t } = useTranslation();
  const upload = useUploadVersion(tableName, id);
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [comment, setComment] = useState("");
  const [reextract, setReextract] = useState(false);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    upload.mutate(
      { file, comment: comment.trim() || undefined, reextract },
      { onSuccess: (result) => onDone(result) },
    );
  }

  return (
    <form onSubmit={submit} className="mb-3 rounded-md border border-gray-200 bg-gray-50 p-3 space-y-2">
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="block w-full text-sm text-gray-700"
      />
      <input
        type="text"
        value={comment}
        maxLength={500}
        onChange={(e) => setComment(e.target.value)}
        placeholder={t("versions.commentPlaceholder")}
        className="h-8 w-full rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
      />
      <label className="flex items-start gap-2 text-xs text-gray-700">
        <input type="checkbox" checked={reextract} onChange={(e) => setReextract(e.target.checked)} className="mt-0.5" />
        <span>
          {t("versions.reextract")}
          <span className="block text-gray-400">{t("versions.reextractHint")}</span>
        </span>
      </label>
      {upload.isError && <p className="text-xs text-red-600">{(upload.error as Error).message}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={() => onDone()} className="text-xs text-gray-500 hover:text-gray-800">
          {t("versions.cancel")}
        </button>
        <button
          type="submit"
          disabled={!file || upload.isPending}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
        >
          {upload.isPending ? t("versions.uploading") : t("versions.upload")}
        </button>
      </div>
    </form>
  );
}

export default function VersionsSection({
  tableName,
  id,
  canDownload,
  canEdit,
}: {
  tableName: string;
  id: string;
  // opening/downloading a file needs download permission, uploading and
  // restoring needs edit (backend-enforced)
  canDownload: boolean;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const { data, isLoading, error } = useDocumentVersions(tableName, id);
  const restore = useRestoreVersion(tableName, id);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function restoreVersion(versionNo: number) {
    const ok = await confirm({
      title: t("versions.restoreTitle", { n: versionNo }),
      message: t("versions.restoreConfirm", { n: versionNo }),
      confirmLabel: t("versions.restore"),
    });
    if (!ok) return;
    restore.mutate(
      { versionNo },
      { onSuccess: (res) => setNotice(t("versions.restored", { n: res.version.version_no })) },
    );
  }

  const versions = data?.versions ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{t("versions.heading")}</h3>
        {canEdit && !uploading && (
          <button
            onClick={() => {
              setUploading(true);
              setNotice(null);
            }}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            {t("versions.addVersion")}
          </button>
        )}
      </div>

      {uploading && (
        <UploadForm
          tableName={tableName}
          id={id}
          onDone={(result) => {
            setUploading(false);
            if (result) {
              setNotice(
                result.batch_id
                  ? t("versions.addedReextracting", { n: result.version.version_no })
                  : t("versions.added", { n: result.version.version_no }),
              );
            }
          }}
        />
      )}
      {notice && <p className="mb-2 text-xs text-emerald-700">{notice}</p>}
      {restore.isError && <p className="mb-2 text-xs text-red-600">{(restore.error as Error).message}</p>}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("versions.loading")}</p>
      ) : error ? (
        <p className="text-sm text-red-500">{t("versions.loadError")}</p>
      ) : versions.length === 0 ? (
        <p className="text-sm text-gray-400">{t("versions.none")}</p>
      ) : (
        versions.map((v) => (
          <VersionRow
            key={v.version_no}
            version={v}
            tableName={tableName}
            id={id}
            canDownload={canDownload}
            canRestore={canEdit}
            onRestore={() => restoreVersion(v.version_no)}
            restoring={restore.isPending}
          />
        ))
      )}
    </div>
  );
}
