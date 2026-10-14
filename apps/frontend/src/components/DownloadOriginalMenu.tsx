import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { documentDownloadUrl } from "@sdai/api-client";
import { useDocumentVersions } from "../hooks/useVersions";
import { useDownloadFile } from "../hooks/useSharing";

/** "Download the original": the current version's untouched file, or any
 *  earlier version from the menu. Fetched (not a plain link) so "still being
 *  synchronised" and other refusals show as a message. Audited server-side. */
export default function DownloadOriginalMenu({ tableName, id }: { tableName: string; id: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const download = useDownloadFile();
  const { data } = useDocumentVersions(tableName, id, open);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function run(versionNo?: number) {
    setOpen(false);
    download.mutate(documentDownloadUrl(tableName, id, versionNo));
  }

  const versions = data?.versions ?? [];

  return (
    <div ref={ref} className="relative flex">
      <button
        onClick={() => run()}
        disabled={download.isPending}
        title={t("download.originalTitle")}
        className="px-2.5 py-1.5 rounded-l-md text-xs font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
      >
        {download.isPending ? t("download.preparing") : t("download.original")}
      </button>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("download.chooseVersion")}
        className="px-1.5 py-1.5 rounded-r-md text-xs border border-l-0 border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
      >
        <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full mt-1 w-64 rounded-md border border-gray-200 bg-white shadow-lg z-20 py-1">
          <p className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
            {t("download.versionsHeading")}
          </p>
          {versions.length === 0 && <p className="px-3 py-1.5 text-xs text-gray-400">{t("download.loadingVersions")}</p>}
          {versions.map((v) => (
            <button
              key={v.version_no}
              role="menuitem"
              onClick={() => run(v.version_no)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
            >
              <span className="font-mono font-semibold">v{v.version_no}</span>
              <span className="flex-1 truncate" title={v.original_filename ?? ""}>
                {v.original_filename ?? t("versions.unnamed")}
              </span>
              {v.is_current && <span className="text-emerald-700">{t("versions.current")}</span>}
            </button>
          ))}
        </div>
      )}
      {download.isError && (
        <p role="alert" className="absolute right-0 top-full mt-1 max-w-xs text-xs text-red-600 bg-white">
          {(download.error as Error).message}
        </p>
      )}
    </div>
  );
}
