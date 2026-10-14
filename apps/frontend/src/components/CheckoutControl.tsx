import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useConfirm } from "../context/ConfirmContext";
import { useDocumentLock, useLockAction } from "../hooks/useCollaboration";
import { formatDateTime } from "../utils/lifecycle";

// Check-out / check-in in the document header (GED Phase 7): the lock badge
// ("Verrouillé par X"), Extraire, Réintégrer (optionally with a new version)
// and, for admins, a forced unlock. What is offered comes from GET .../lock -
// the backend stays the authority.
export default function CheckoutControl({ tableName, id }: { tableName: string; id: string }) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const { data: lock } = useDocumentLock(tableName, id);
  const action = useLockAction(tableName, id);
  const [checkinOpen, setCheckinOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [reextract, setReextract] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  if (!lock) return null;
  const until = formatDateTime(lock.locked_until, i18n.language);

  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : String(e));
  }

  async function forceUnlock() {
    const ok = await confirm({
      title: t("checkout.forceUnlock"),
      message: t("checkout.forceUnlockConfirm", { name: lock?.locked_by_name ?? "?" }),
      confirmLabel: t("checkout.forceUnlock"),
    });
    if (ok) action.mutate({ kind: "unlock" }, { onError: fail });
  }

  function checkin() {
    setError(null);
    action.mutate(
      {
        kind: "checkin",
        file: fileRef.current?.files?.[0] ?? null,
        comment: comment.trim() || undefined,
        reextract,
      },
      {
        onSuccess: () => {
          setCheckinOpen(false);
          setComment("");
          setReextract(false);
        },
        onError: fail,
      },
    );
  }

  return (
    <div className="relative flex items-center gap-2">
      {lock.locked && !lock.mine && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-100 text-amber-800 text-xs font-medium"
          title={t("checkout.until", { date: until })}
        >
          <LockIcon />
          {t("checkout.lockedBy", { name: lock.locked_by_name ?? "?" })}
        </span>
      )}
      {lock.mine && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-sky-100 text-sky-800 text-xs font-medium"
          title={t("checkout.until", { date: until })}
        >
          <LockIcon />
          {t("checkout.lockedByMe")}
        </span>
      )}
      {!lock.locked && lock.can_checkout && (
        <button
          onClick={() => action.mutate({ kind: "checkout" }, { onError: fail })}
          disabled={action.isPending}
          className="px-2.5 py-1.5 rounded-md text-xs font-medium border border-gray-300 text-gray-700 hover:bg-gray-50"
          title={t("checkout.checkoutHint", { hours: lock.default_hours })}
        >
          {t("checkout.checkout")}
        </button>
      )}
      {lock.can_checkin && (
        <button
          onClick={() => setCheckinOpen((o) => !o)}
          disabled={action.isPending}
          className="px-2.5 py-1.5 rounded-md text-xs font-semibold bg-sky-600 text-white hover:bg-sky-700"
        >
          {t("checkout.checkin")}
        </button>
      )}
      {lock.can_force_unlock && (
        <button
          onClick={forceUnlock}
          disabled={action.isPending}
          className="text-xs font-medium text-red-600 hover:text-red-800"
        >
          {t("checkout.forceUnlock")}
        </button>
      )}
      {error && !checkinOpen && <span className="text-xs text-red-600 max-w-48 truncate" title={error}>{error}</span>}

      {checkinOpen && (
        <div className="absolute right-0 top-full mt-2 z-20 w-80 rounded-lg border border-gray-200 bg-white shadow-lg p-3 space-y-2">
          <p className="text-xs text-gray-600">{t("checkout.checkinHint")}</p>
          <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" className="block w-full text-xs" />
          <input
            type="text"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder={t("checkout.comment")}
            maxLength={500}
            className="h-8 w-full rounded-md border border-gray-300 px-2 text-sm"
          />
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input type="checkbox" checked={reextract} onChange={(e) => setReextract(e.target.checked)} />
            {t("checkout.reextract")}
          </label>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={() => setCheckinOpen(false)} className="px-3 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100">
              {t("common.cancel")}
            </button>
            <button
              onClick={checkin}
              disabled={action.isPending}
              className="px-3 py-1 rounded-md text-xs font-semibold text-white bg-sky-600 hover:bg-sky-700 disabled:opacity-50"
            >
              {t("checkout.checkin")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function LockIcon() {
  return (
    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
    </svg>
  );
}
