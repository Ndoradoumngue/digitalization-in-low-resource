import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { SHARE_MAX_DAYS, type ShareLink } from "@sdai/types";
import { useConfirm } from "../context/ConfirmContext";
import { useDocumentVersions } from "../hooks/useVersions";
import { useCreateShare, useDocumentShares, useRevokeShare } from "../hooks/useSharing";

// External share links for one document. The full link exists only in the
// create response - the server keeps a hash - so it is shown once, here,
// with a copy button; the list below shows every link's state, never the
// link itself.

const EXPIRY_CHOICES = [1, 3, 7, 14, SHARE_MAX_DAYS];

const STATUS_STYLE: Record<ShareLink["status"], string> = {
  active: "bg-emerald-100 text-emerald-800",
  expired: "bg-gray-100 text-gray-600",
  revoked: "bg-red-100 text-red-700",
  exhausted: "bg-amber-100 text-amber-800",
};

export default function ShareButton({ tableName, id }: { tableName: string; id: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={t("share.buttonTitle")}
        className="px-2.5 py-1.5 rounded-md text-xs font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
      >
        {t("share.button")}
      </button>
      {open && <ShareDialog tableName={tableName} id={id} onClose={() => setOpen(false)} />}
    </>
  );
}

function ShareDialog({ tableName, id, onClose }: { tableName: string; id: string; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const shares = useDocumentShares(tableName, id);
  const versions = useDocumentVersions(tableName, id);
  const create = useCreateShare(tableName, id);
  const revoke = useRevokeShare(tableName, id);

  const [days, setDays] = useState(7);
  const [password, setPassword] = useState("");
  const [maxDownloads, setMaxDownloads] = useState("");
  const [versionNo, setVersionNo] = useState("");
  const [note, setNote] = useState("");
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const urlRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setCopied(false);
    create.mutate(
      {
        expires_in_days: days,
        ...(password && { password }),
        ...(maxDownloads && { max_downloads: Number(maxDownloads) }),
        ...(versionNo && { version_no: Number(versionNo) }),
        ...(note.trim() && { note: note.trim() }),
      },
      {
        onSuccess: (result) => {
          setCreatedUrl(result.url);
          setPassword("");
        },
      },
    );
  }

  async function copy() {
    if (!createdUrl) return;
    try {
      await navigator.clipboard.writeText(createdUrl);
      setCopied(true);
    } catch {
      // Clipboard API refused (insecure context) - select the text instead.
      urlRef.current?.select();
    }
  }

  async function onRevoke(share: ShareLink) {
    if (!(await confirm({ message: t("share.revokeConfirm"), danger: true, confirmLabel: t("share.revoke") }))) return;
    revoke.mutate(share.id);
  }

  const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString(i18n.language) : "");
  const label = "block text-xs font-medium text-gray-600 mb-1";
  const input = "h-8 w-full rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      <div className="absolute inset-0 bg-gray-900/40" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-dialog-title"
        className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl bg-white shadow-xl border border-gray-200 p-5"
      >
        <div className="flex items-start justify-between gap-3 mb-1">
          <h2 id="share-dialog-title" className="text-sm font-bold text-gray-900">{t("share.title")}</h2>
          <button onClick={onClose} aria-label={t("share.close")} className="text-gray-400 hover:text-gray-700">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">{t("share.intro")}</p>

        {createdUrl ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 mb-4">
            <p className="text-xs font-semibold text-emerald-900 mb-2">{t("share.createdOnce")}</p>
            <div className="flex gap-2">
              <input
                ref={urlRef}
                readOnly
                value={createdUrl}
                onFocus={(e) => e.target.select()}
                aria-label={t("share.link")}
                className="h-8 flex-1 min-w-0 rounded-md border border-emerald-300 bg-white px-2 text-xs font-mono"
              />
              <button
                onClick={copy}
                className="px-3 h-8 rounded-md text-xs font-semibold bg-emerald-600 text-white hover:bg-emerald-700"
              >
                {copied ? t("share.copied") : t("share.copy")}
              </button>
            </div>
            <button onClick={() => setCreatedUrl(null)} className="mt-2 text-xs font-medium text-emerald-800 hover:underline">
              {t("share.another")}
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="grid grid-cols-2 gap-3 mb-4">
            <div>
              <label className={label} htmlFor="share-days">{t("share.expiry")}</label>
              <select id="share-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className={input}>
                {EXPIRY_CHOICES.map((d) => (
                  <option key={d} value={d}>{t("share.days", { count: d })}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={label} htmlFor="share-max">{t("share.maxDownloads")}</label>
              <input
                id="share-max"
                type="number"
                min={1}
                max={1000}
                value={maxDownloads}
                onChange={(e) => setMaxDownloads(e.target.value)}
                placeholder={t("share.unlimited")}
                className={input}
              />
            </div>
            <div>
              <label className={label} htmlFor="share-pw">{t("share.password")}</label>
              <input
                id="share-pw"
                type="password"
                minLength={4}
                maxLength={128}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("share.optional")}
                className={input}
              />
            </div>
            <div>
              <label className={label} htmlFor="share-version">{t("share.version")}</label>
              <select id="share-version" value={versionNo} onChange={(e) => setVersionNo(e.target.value)} className={input}>
                <option value="">{t("share.latest")}</option>
                {(versions.data?.versions ?? []).map((v) => (
                  <option key={v.version_no} value={v.version_no}>
                    {t("share.pinVersion", { n: v.version_no })}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-span-2">
              <label className={label} htmlFor="share-note">{t("share.note")}</label>
              <input
                id="share-note"
                maxLength={200}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t("share.notePlaceholder")}
                className={input}
              />
            </div>
            <div className="col-span-2 flex items-center justify-between gap-3">
              {create.isError ? (
                <p role="alert" className="text-xs text-red-600">{(create.error as Error).message}</p>
              ) : (
                <span className="text-[11px] text-gray-400">{t("share.audited")}</span>
              )}
              <button
                type="submit"
                disabled={create.isPending}
                className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {create.isPending ? t("share.creating") : t("share.create")}
              </button>
            </div>
          </form>
        )}

        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">{t("share.existing")}</h3>
        {shares.isLoading ? (
          <p className="text-xs text-gray-400">{t("share.loading")}</p>
        ) : (shares.data?.shares ?? []).length === 0 ? (
          <p className="text-xs text-gray-400">{t("share.none")}</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {shares.data!.shares.map((s) => (
              <li key={s.id} className="py-2 flex items-start gap-2">
                <span className={`mt-0.5 inline-flex px-1.5 py-0.5 rounded text-[11px] font-semibold ${STATUS_STYLE[s.status]}`}>
                  {t(`share.status.${s.status}`)}
                </span>
                <div className="flex-1 min-w-0 text-xs text-gray-600">
                  <p className="text-gray-800 truncate">{s.note || t("share.noNote")}</p>
                  <p className="flex flex-wrap gap-x-3">
                    <span>{t("share.until", { date: fmt(s.expires_at) })}</span>
                    <span>
                      {s.max_downloads != null
                        ? t("share.downloadsOf", { count: s.download_count, max: s.max_downloads })
                        : t("share.downloads", { count: s.download_count })}
                    </span>
                    {s.has_password && <span>{t("share.withPassword")}</span>}
                    <span>{s.version_no != null ? t("share.pinVersion", { n: s.version_no }) : t("share.latest")}</span>
                    {s.created_by_email && <span>{s.created_by_email}</span>}
                  </p>
                </div>
                {s.status === "active" && (
                  <button
                    onClick={() => onRevoke(s)}
                    disabled={revoke.isPending}
                    className="text-xs font-medium text-red-600 hover:text-red-800 disabled:opacity-40"
                  >
                    {t("share.revoke")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
