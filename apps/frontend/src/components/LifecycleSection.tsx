import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useConfirm } from "../context/ConfirmContext";
import { useCreateLegalHold, useDocumentLifecycle, useLifecycleAction, useReleaseLegalHold } from "../hooks/useLifecycle";
import { formatDate } from "../utils/lifecycle";
import LifecycleBadge from "./LifecycleBadge";

type Form = null | "trash" | "extend" | "hold" | { release: string };

const inputClass =
  "h-8 w-full rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

// "Cycle de vie" on the record tab: status, retention date and rule, legal
// holds, and the archivist actions the backend says this user may take
// (GET .../lifecycle -> actions). The backend stays the authority.
export default function LifecycleSection({ tableName, id }: { tableName: string; id: string }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const { data } = useDocumentLifecycle(tableName, id);
  const action = useLifecycleAction(tableName, id);
  const hold = useCreateLegalHold();
  const release = useReleaseLegalHold();
  const [form, setForm] = useState<Form>(null);
  const [text, setText] = useState("");
  const [until, setUntil] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!data) return null;
  const can = (a: string) => data.actions.includes(a as never);
  const busy = action.isPending || hold.isPending || release.isPending;

  function open(next: Form) {
    setForm(next);
    setText("");
    setUntil("");
    setError(null);
  }

  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : String(e));
  }

  async function archive() {
    const ok = await confirm({
      title: t("lifecycle.archive"),
      message: t("lifecycle.archiveConfirm"),
      confirmLabel: t("lifecycle.archive"),
    });
    if (ok) action.mutate({ kind: "archive" }, { onError: fail });
  }

  function submit() {
    setError(null);
    if (form === "trash") {
      action.mutate(
        { kind: "trash", reason: text.trim() || undefined },
        { onSuccess: () => navigate("/documents"), onError: fail },
      );
    } else if (form === "extend") {
      action.mutate({ kind: "extend", until, reason: text.trim() || undefined }, { onSuccess: () => open(null), onError: fail });
    } else if (form === "hold") {
      hold.mutate(
        { reason: text.trim(), document: { table_name: tableName, id } },
        { onSuccess: () => open(null), onError: fail },
      );
    } else if (form && typeof form === "object") {
      release.mutate({ holdId: form.release, reason: text.trim() }, { onSuccess: () => open(null), onError: fail });
    }
  }

  const needsText = form === "hold" || (form !== null && typeof form === "object");
  const valid =
    (!needsText || text.trim().length >= 3) && (form !== "extend" || !!until);

  return (
    <div className="pt-4 mt-4 border-t border-gray-200">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{t("lifecycle.heading")}</h3>
        <div className="flex items-center gap-3">
          {can("archive") && (
            <button onClick={archive} disabled={busy} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
              {t("lifecycle.archive")}
            </button>
          )}
          {can("extend") && (
            <button onClick={() => open("extend")} disabled={busy} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
              {t("lifecycle.extend")}
            </button>
          )}
          {can("hold") && (
            <button onClick={() => open("hold")} disabled={busy} className="text-xs font-medium text-purple-700 hover:text-purple-900">
              {t("lifecycle.placeHold")}
            </button>
          )}
          {can("trash") && (
            <button onClick={() => open("trash")} disabled={busy} className="text-xs font-medium text-red-600 hover:text-red-800">
              {t("lifecycle.trash")}
            </button>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-gray-500">{t("lifecycle.statusLabel")}</dt>
        <dd className="flex items-center gap-1.5">
          {data.lifecycle_status === "active" ? (
            <span className="text-gray-700">{t("lifecycle.status.active")}</span>
          ) : (
            <LifecycleBadge status={data.lifecycle_status} />
          )}
          {data.archived_at && (
            <span className="text-xs text-gray-400">{t("lifecycle.since", { date: formatDate(data.archived_at, i18n.language) })}</span>
          )}
        </dd>
        <dt className="text-gray-500">{t("lifecycle.retentionUntil")}</dt>
        <dd className="text-gray-700">
          {data.retention_until ? formatDate(data.retention_until, i18n.language) : (
            <span className="text-gray-400">
              {data.retention_rule?.final_action === "keep_forever" ? t("lifecycle.keepForever") : t("lifecycle.noRetention")}
            </span>
          )}
          {data.retention_override && <span className="ml-1.5 text-xs text-gray-400">({t("lifecycle.manualDate")})</span>}
        </dd>
        {data.retention_rule && (
          <>
            <dt className="text-gray-500">{t("lifecycle.rule")}</dt>
            <dd className="text-gray-700">
              {data.retention_rule.name}{" "}
              <span className="text-xs text-gray-400">
                ({t("retention.durationValue", { count: data.retention_rule.duration_months })},{" "}
                {t(`retention.finalAction.${data.retention_rule.final_action}`)})
              </span>
            </dd>
          </>
        )}
      </dl>

      {data.holds.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {data.holds.map((h) => (
            <li key={h.id} className="rounded-md border border-purple-200 bg-purple-50 px-3 py-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-purple-900">
                  {t("lifecycle.legalHold")}
                  {h.series_name && <span className="font-normal"> · {t("lifecycle.holdOnSeries", { name: h.series_name })}</span>}
                  {h.origin === "search" && <span className="font-normal"> · {t("lifecycle.holdOnSearch")}</span>}
                </span>
                {can("release_hold") && (
                  <button
                    onClick={() => open({ release: h.id })}
                    disabled={busy}
                    className="text-xs font-medium text-purple-700 hover:text-purple-900"
                  >
                    {t("lifecycle.releaseHold")}
                  </button>
                )}
              </div>
              <p className="text-purple-800 text-xs mt-0.5">{h.reason}</p>
              <p className="text-purple-600 text-[11px] mt-0.5">
                {h.created_by} · {h.created_at ? formatDate(h.created_at, i18n.language) : ""}
              </p>
            </li>
          ))}
        </ul>
      )}

      {form !== null && (
        <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2">
          <p className="text-xs text-gray-600">
            {form === "trash" && t("lifecycle.trashHint")}
            {form === "extend" && t("lifecycle.extendHint")}
            {form === "hold" && t("lifecycle.holdHint")}
            {typeof form === "object" && t("lifecycle.releaseHint")}
          </p>
          {form === "extend" && (
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} className={inputClass} aria-label={t("lifecycle.newDate")} />
          )}
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={needsText ? t("lifecycle.reasonRequired") : t("lifecycle.reasonOptional")}
            className={inputClass}
            maxLength={1000}
            autoFocus
          />
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={() => open(null)} className="px-3 py-1 rounded-md text-xs text-gray-600 hover:bg-gray-100">
              {t("lifecycle.cancel")}
            </button>
            <button
              onClick={submit}
              disabled={!valid || busy}
              className={`px-3 py-1 rounded-md text-xs font-semibold text-white disabled:opacity-50 ${
                form === "trash" ? "bg-red-600 hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700"
              }`}
            >
              {form === "trash" && t("lifecycle.trash")}
              {form === "extend" && t("lifecycle.extend")}
              {form === "hold" && t("lifecycle.placeHold")}
              {typeof form === "object" && t("lifecycle.releaseHold")}
            </button>
          </div>
        </div>
      )}
      {form === null && error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
