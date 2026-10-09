import { useTranslation } from "react-i18next";
import type { ChangeSource, DocumentEvent, FieldChange } from "@sdai/types";
import { useDocumentHistory } from "../hooks/useVersions";
import { labelFor } from "../utils/format";

// What happened to this document: every field change with its before and
// after value (who, how - edit/review/re-extraction - and under which file
// version), then the document's audit events. Read-only; the backend hides
// view/download tracking from users who don't manage access.

const SOURCE_STYLES: Record<ChangeSource, string> = {
  edit: "bg-violet-100 text-violet-800",
  review: "bg-emerald-100 text-emerald-800",
  reextract: "bg-blue-100 text-blue-800",
  sync: "bg-gray-100 text-gray-700",
};

function renderValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "∅";
  if (Array.isArray(v)) {
    return v.some((item) => item !== null && typeof item === "object")
      ? JSON.stringify(v)
      : v.map(String).join(", ");
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function FieldChangeRow({ change }: { change: FieldChange }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="py-2 border-b border-gray-100 last:border-0">
      <div className="flex items-center gap-2 text-xs">
        <span className="font-semibold text-gray-800">{labelFor(change.field)}</span>
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded font-medium ${SOURCE_STYLES[change.change_source]}`}>
          {t(`history.source.${change.change_source}`)}
        </span>
        {change.version_no != null && <span className="font-mono text-gray-400">v{change.version_no}</span>}
      </div>
      <div className="mt-1 text-sm break-words">
        <span className="text-red-700 line-through decoration-red-300">{renderValue(change.old_value)}</span>
        <span className="mx-1.5 text-gray-400">→</span>
        <span className="text-emerald-800">{renderValue(change.new_value)}</span>
      </div>
      <div className="mt-0.5 text-[11px] text-gray-500">
        {new Date(change.created_at).toLocaleString(i18n.language)}
        {" · "}
        {change.changed_by_email ?? t("history.system")}
      </div>
    </div>
  );
}

function EventRow({ event }: { event: DocumentEvent }) {
  const { t, i18n } = useTranslation();
  const version = event.details?.version_no;
  return (
    <div className="flex items-baseline gap-2 py-1.5 border-b border-gray-100 last:border-0 text-xs">
      <span className="text-gray-500 whitespace-nowrap">{new Date(event.created_at).toLocaleString(i18n.language)}</span>
      <span className="font-medium text-gray-800">
        {t(`audit.actions.${event.action}`, event.action.replace(/_/g, " "))}
        {typeof version === "number" && <span className="font-mono text-gray-400"> v{version}</span>}
      </span>
      <span className="ml-auto text-gray-500 truncate">{event.user_email ?? t("history.system")}</span>
    </div>
  );
}

export default function HistorySection({ tableName, id }: { tableName: string; id: string }) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useDocumentHistory(tableName, id);

  if (isLoading) return <p className="text-sm text-gray-400">{t("history.loading")}</p>;
  if (error) return <p className="text-sm text-red-500">{t("history.loadError")}</p>;

  const fields = data?.fields ?? [];
  const events = data?.events ?? [];
  return (
    <div className="space-y-6">
      <section>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
          {t("history.fieldChanges")}
        </h3>
        {fields.length === 0 ? (
          <p className="text-sm text-gray-400">{t("history.noFieldChanges")}</p>
        ) : (
          fields.map((c) => <FieldChangeRow key={c.id} change={c} />)
        )}
      </section>
      <section>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
          {t("history.events")}
        </h3>
        {events.length === 0 ? (
          <p className="text-sm text-gray-400">{t("history.noEvents")}</p>
        ) : (
          events.map((e) => <EventRow key={e.id} event={e} />)
        )}
      </section>
    </div>
  );
}
