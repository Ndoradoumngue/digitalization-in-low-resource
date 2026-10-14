import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FINAL_ACTIONS,
  RETENTION_TRIGGERS,
  type FinalAction,
  type RetentionRule,
  type RetentionRuleBody,
  type RetentionTrigger,
} from "@sdai/types";
import NavSidebar from "../components/NavSidebar";
import { useAuth } from "../context/AuthContext";
import { useConfirm } from "../context/ConfirmContext";
import { useDbTypes } from "../hooks/useDocumentsDb";
import {
  useCreateLegalHold,
  useDeleteRetentionRule,
  useLegalHolds,
  useReleaseLegalHold,
  useRetentionRules,
  useRunRetentionJob,
  useSaveRetentionRule,
} from "../hooks/useLifecycle";
import { useSeries } from "../hooks/useSeries";
import { formatDate } from "../utils/lifecycle";
import { flattenTree, seriesLabel } from "../utils/series";

const inputClass =
  "h-8 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

const EMPTY: RetentionRuleBody = {
  name: "",
  scope: "series",
  duration_months: 120,
  trigger_on: "ingested_at",
  final_action: "destroy",
};

// "Règles de conservation": how long each series / document type is kept and
// what happens next, plus the legal holds that suspend it.
export default function RetentionRulesPage() {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const confirm = useConfirm();
  const { data: rules, isLoading, error } = useRetentionRules();
  const { data: series } = useSeries();
  const { data: types } = useDbTypes();
  const save = useSaveRetentionRule();
  const remove = useDeleteRetentionRule();
  const run = useRunRetentionJob();
  const [editing, setEditing] = useState<{ id?: string; body: RetentionRuleBody } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function startEdit(rule?: RetentionRule) {
    setFormError(null);
    setEditing(
      rule
        ? {
            id: rule.id,
            body: {
              name: rule.name,
              scope: rule.scope,
              series_id: rule.series_id ?? undefined,
              table_name: rule.table_name ?? undefined,
              duration_months: rule.duration_months,
              trigger_on: rule.trigger_on,
              date_field: rule.date_field ?? undefined,
              final_action: rule.final_action,
            },
          }
        : { body: { ...EMPTY } },
    );
  }

  function set<K extends keyof RetentionRuleBody>(key: K, value: RetentionRuleBody[K]) {
    setEditing((e) => (e ? { ...e, body: { ...e.body, [key]: value } } : e));
  }

  function submit() {
    if (!editing) return;
    setFormError(null);
    save.mutate(editing, {
      onSuccess: () => setEditing(null),
      onError: (e) => setFormError((e as Error).message),
    });
  }

  async function onDelete(rule: RetentionRule) {
    if (!(await confirm({ message: t("retention.deleteConfirm", { name: rule.name }), danger: true, confirmLabel: t("retention.delete") }))) return;
    remove.mutate(rule.id);
  }

  const typeLabel = (table: string | null) =>
    types?.find((x) => x.table_name === table)?.document_type ?? table ?? "";

  const body = editing?.body;
  const valid =
    !!body &&
    body.name.trim() &&
    (body.scope === "series" ? !!body.series_id : !!body.table_name) &&
    (body.trigger_on !== "document_date" || !!body.date_field?.trim());

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0 flex items-center gap-3">
          <div className="flex-1">
            <h1 className="text-base font-bold text-indigo-700 tracking-tight">{t("retention.header")}</h1>
            <p className="text-xs text-gray-500 mt-0.5">{t("retention.intro")}</p>
          </div>
          {user?.role === "admin" && (
            <button
              onClick={() => run.mutate()}
              disabled={run.isPending}
              className="px-3 py-1.5 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              title={t("retention.runHint")}
            >
              {t("retention.runNow")}
            </button>
          )}
          <button
            onClick={() => startEdit()}
            className="px-3 py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700"
          >
            {t("retention.add")}
          </button>
        </header>
        <div className="flex-1 overflow-auto p-6 space-y-6">
          {run.data && (
            <p className="text-xs text-emerald-700">{t("retention.runResult", run.data)}</p>
          )}
          {run.isError && <p className="text-xs text-red-600">{(run.error as Error).message}</p>}

          {editing && body && (
            <div className="max-w-3xl bg-white rounded-lg border border-indigo-200 p-4 space-y-3">
              <h2 className="text-sm font-semibold text-gray-800">
                {editing.id ? t("retention.edit") : t("retention.add")}
              </h2>
              <div className="grid grid-cols-2 gap-3">
                <label className="col-span-2 text-xs text-gray-600">
                  {t("retention.name")}
                  <input value={body.name} onChange={(e) => set("name", e.target.value)} className={`${inputClass} w-full mt-1`} maxLength={200} />
                </label>
                <label className="text-xs text-gray-600">
                  {t("retention.scope")}
                  <select
                    value={body.scope}
                    disabled={!!editing.id}
                    onChange={(e) => set("scope", e.target.value as "series" | "doc_type")}
                    className={`${inputClass} w-full mt-1`}
                  >
                    <option value="series">{t("retention.scopeSeries")}</option>
                    <option value="doc_type">{t("retention.scopeDocType")}</option>
                  </select>
                </label>
                <label className="text-xs text-gray-600">
                  {body.scope === "series" ? t("retention.series") : t("retention.docType")}
                  {body.scope === "series" ? (
                    <select
                      value={body.series_id ?? ""}
                      disabled={!!editing.id}
                      onChange={(e) => set("series_id", e.target.value || undefined)}
                      className={`${inputClass} w-full mt-1`}
                    >
                      <option value="">—</option>
                      {flattenTree(series ?? []).map(({ series: s, depth }) => (
                        <option key={s.id} value={s.id}>
                          {"  ".repeat(depth)}
                          {seriesLabel(s, t)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <select
                      value={body.table_name ?? ""}
                      disabled={!!editing.id}
                      onChange={(e) => set("table_name", e.target.value || undefined)}
                      className={`${inputClass} w-full mt-1`}
                    >
                      <option value="">—</option>
                      {(types ?? []).map((ty) => (
                        <option key={ty.table_name} value={ty.table_name}>{ty.document_type}</option>
                      ))}
                    </select>
                  )}
                </label>
                <label className="text-xs text-gray-600">
                  {t("retention.duration")}
                  <input
                    type="number"
                    min={0}
                    max={1200}
                    value={body.duration_months}
                    onChange={(e) => set("duration_months", Math.max(0, parseInt(e.target.value, 10) || 0))}
                    className={`${inputClass} w-full mt-1`}
                  />
                </label>
                <label className="text-xs text-gray-600">
                  {t("retention.trigger")}
                  <select
                    value={body.trigger_on}
                    onChange={(e) => set("trigger_on", e.target.value as RetentionTrigger)}
                    className={`${inputClass} w-full mt-1`}
                  >
                    {RETENTION_TRIGGERS.map((tr) => (
                      <option key={tr} value={tr}>{t(`retention.triggerOn.${tr}`)}</option>
                    ))}
                  </select>
                </label>
                {body.trigger_on === "document_date" && (
                  <label className="text-xs text-gray-600">
                    {t("retention.dateField")}
                    <input
                      value={body.date_field ?? ""}
                      onChange={(e) => set("date_field", e.target.value)}
                      placeholder="date_document"
                      className={`${inputClass} w-full mt-1 font-mono`}
                    />
                  </label>
                )}
                <label className="text-xs text-gray-600">
                  {t("retention.finalActionLabel")}
                  <select
                    value={body.final_action}
                    onChange={(e) => set("final_action", e.target.value as FinalAction)}
                    className={`${inputClass} w-full mt-1`}
                  >
                    {FINAL_ACTIONS.map((fa) => (
                      <option key={fa} value={fa}>{t(`retention.finalAction.${fa}`)}</option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="text-xs text-gray-500">{t("retention.precedence")}</p>
              {formError && <p className="text-xs text-red-600">{formError}</p>}
              <div className="flex justify-end gap-2">
                <button onClick={() => setEditing(null)} className="px-3 py-1.5 rounded-md text-xs text-gray-600 hover:bg-gray-100">
                  {t("retention.cancel")}
                </button>
                <button
                  onClick={submit}
                  disabled={!valid || save.isPending}
                  className="px-3 py-1.5 rounded-md text-xs font-semibold bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {t("retention.save")}
                </button>
              </div>
            </div>
          )}

          {error ? (
            <p className="text-sm text-red-500">{t("retention.loadError")}</p>
          ) : isLoading ? (
            <p className="text-sm text-gray-400">{t("retention.loading")}</p>
          ) : (rules ?? []).length === 0 ? (
            <p className="text-sm text-gray-400">{t("retention.none")}</p>
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden max-w-5xl">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                  <tr>
                    <th className="text-left px-4 py-2">{t("retention.name")}</th>
                    <th className="text-left px-4 py-2">{t("retention.appliesTo")}</th>
                    <th className="text-left px-4 py-2">{t("retention.duration")}</th>
                    <th className="text-left px-4 py-2">{t("retention.finalActionLabel")}</th>
                    <th className="text-right px-4 py-2">{t("retention.documents")}</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(rules ?? []).map((rule) => (
                    <tr key={rule.id}>
                      <td className="px-4 py-2 font-medium text-gray-800">{rule.name}</td>
                      <td className="px-4 py-2 text-gray-700">
                        <span className="text-xs text-gray-400 mr-1">
                          {rule.scope === "series" ? t("retention.scopeSeries") : t("retention.scopeDocType")}
                        </span>
                        {rule.scope === "series"
                          ? [rule.series_code, rule.series_name].filter(Boolean).join(" ")
                          : typeLabel(rule.table_name)}
                      </td>
                      <td className="px-4 py-2 text-gray-700">
                        {t("retention.durationValue", { count: rule.duration_months })}
                        <div className="text-xs text-gray-400">
                          {t(`retention.triggerOn.${rule.trigger_on}`)}
                          {rule.date_field && <span className="font-mono"> ({rule.date_field})</span>}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-gray-700">{t(`retention.finalAction.${rule.final_action}`)}</td>
                      <td className="px-4 py-2 text-right text-gray-600">{rule.document_count ?? 0}</td>
                      <td className="px-4 py-2 text-right whitespace-nowrap">
                        <button onClick={() => startEdit(rule)} className="text-xs font-medium text-indigo-600 hover:text-indigo-800 mr-3">
                          {t("retention.editShort")}
                        </button>
                        <button onClick={() => onDelete(rule)} disabled={remove.isPending} className="text-xs font-medium text-red-600 hover:text-red-800">
                          {t("retention.delete")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <LegalHoldsPanel locale={i18n.language} />
        </div>
      </div>
    </div>
  );
}

function LegalHoldsPanel({ locale }: { locale: string }) {
  const { t } = useTranslation();
  const [showReleased, setShowReleased] = useState(false);
  const { data: holds } = useLegalHolds(showReleased);
  const { data: series } = useSeries();
  const create = useCreateLegalHold();
  const release = useReleaseLegalHold();
  const [seriesId, setSeriesId] = useState("");
  const [reason, setReason] = useState("");
  const [releasing, setReleasing] = useState<string | null>(null);
  const [releaseReason, setReleaseReason] = useState("");
  const [err, setErr] = useState<string | null>(null);

  return (
    <section className="max-w-5xl">
      <div className="flex items-center gap-3 mb-2">
        <h2 className="text-sm font-bold text-gray-800">{t("legalHolds.header")}</h2>
        <label className="ml-auto text-xs text-gray-500 flex items-center gap-1">
          <input type="checkbox" checked={showReleased} onChange={(e) => setShowReleased(e.target.checked)} />
          {t("legalHolds.showReleased")}
        </label>
      </div>
      <p className="text-xs text-gray-500 mb-3">{t("legalHolds.intro")}</p>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select value={seriesId} onChange={(e) => setSeriesId(e.target.value)} className={inputClass}>
          <option value="">{t("legalHolds.pickSeries")}</option>
          {flattenTree(series ?? []).map(({ series: s, depth }) => (
            <option key={s.id} value={s.id}>
              {"  ".repeat(depth)}
              {seriesLabel(s, t)}
            </option>
          ))}
        </select>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t("lifecycle.reasonRequired")}
          className={`${inputClass} flex-1 min-w-[200px]`}
        />
        <button
          disabled={!seriesId || reason.trim().length < 3 || create.isPending}
          onClick={() => {
            setErr(null);
            create.mutate(
              { series_id: seriesId, reason: reason.trim() },
              { onSuccess: () => { setSeriesId(""); setReason(""); }, onError: (e) => setErr((e as Error).message) },
            );
          }}
          className="px-3 py-1.5 rounded-md text-xs font-semibold bg-purple-700 text-white hover:bg-purple-800 disabled:opacity-50"
        >
          {t("legalHolds.holdSeries")}
        </button>
      </div>
      {err && <p className="text-xs text-red-600 mb-2">{err}</p>}
      {(holds ?? []).length === 0 ? (
        <p className="text-sm text-gray-400">{t("legalHolds.none")}</p>
      ) : (
        <ul className="space-y-2">
          {(holds ?? []).map((h) => (
            <li key={h.id} className={`rounded-lg border p-3 text-sm ${h.released_at ? "border-gray-200 bg-white" : "border-purple-200 bg-purple-50"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-gray-800">
                  {h.origin === "series"
                    ? t("lifecycle.holdOnSeries", { name: h.series_name ?? "" })
                    : h.origin === "search"
                      ? t("legalHolds.onSearch", { count: h.document_count ?? 0 })
                      : t("legalHolds.onDocuments", { count: h.document_count ?? 0 })}
                </span>
                <span className="text-xs text-gray-500">
                  {h.created_by} · {formatDate(h.created_at, locale)}
                </span>
                {h.released_at ? (
                  <span className="ml-auto text-xs text-gray-500">
                    {t("legalHolds.released", { date: formatDate(h.released_at, locale), by: h.released_by ?? "" })}
                  </span>
                ) : (
                  <button
                    onClick={() => { setReleasing(h.id); setReleaseReason(""); }}
                    className="ml-auto text-xs font-medium text-purple-700 hover:text-purple-900"
                  >
                    {t("lifecycle.releaseHold")}
                  </button>
                )}
              </div>
              <p className="text-xs text-gray-700 mt-1">{h.reason}</p>
              {h.release_reason && <p className="text-xs text-gray-500 mt-0.5">↳ {h.release_reason}</p>}
              {releasing === h.id && (
                <div className="mt-2 flex gap-2">
                  <input
                    value={releaseReason}
                    onChange={(e) => setReleaseReason(e.target.value)}
                    placeholder={t("lifecycle.reasonRequired")}
                    className={`${inputClass} flex-1`}
                    autoFocus
                  />
                  <button onClick={() => setReleasing(null)} className="px-2 text-xs text-gray-600">{t("lifecycle.cancel")}</button>
                  <button
                    disabled={releaseReason.trim().length < 3 || release.isPending}
                    onClick={() =>
                      release.mutate(
                        { holdId: h.id, reason: releaseReason.trim() },
                        { onSuccess: () => setReleasing(null), onError: (e) => setErr((e as Error).message) },
                      )
                    }
                    className="px-3 py-1 rounded-md text-xs font-semibold bg-purple-700 text-white disabled:opacity-50"
                  >
                    {t("lifecycle.releaseHold")}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
