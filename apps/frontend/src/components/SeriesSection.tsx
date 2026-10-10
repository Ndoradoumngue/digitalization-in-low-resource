import { useState } from "react";
import { useTranslation } from "react-i18next";
import { seriesTrail } from "@sdai/types";
import { useSeries, useAssignDocumentSeries } from "../hooks/useSeries";
import { flattenTree, seriesLabel } from "../utils/series";

export default function SeriesSection({
  tableName,
  id,
  seriesId,
  canEdit = true,
}: {
  tableName: string;
  id: string;
  seriesId: string | null;
  // filing needs edit permission on this document (backend-enforced)
  canEdit?: boolean;
}) {
  const { t } = useTranslation();
  const { data: series } = useSeries();
  const assignMutation = useAssignDocumentSeries(tableName, id);
  const [editing, setEditing] = useState(false);

  // Breadcrumb through the plan de classement, root first.
  const trail = seriesTrail(series ?? [], seriesId);

  function assign(value: string) {
    assignMutation.mutate(value || null, { onSuccess: () => setEditing(false) });
  }

  return (
    <div className="pt-4 mt-4 border-t border-gray-200">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          {t("series.heading")}
        </h3>
        {canEdit && !editing && (
          <button
            onClick={() => setEditing(true)}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            {seriesId ? t("series.change") : t("series.assign")}
          </button>
        )}
      </div>

      {editing ? (
        <div className="flex items-center gap-2">
          <select
            defaultValue={seriesId ?? ""}
            onChange={(e) => assign(e.target.value)}
            autoFocus
            disabled={assignMutation.isPending}
            className="h-8 flex-1 rounded-md border border-gray-300 px-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            <option value="">{t("series.none")}</option>
            {flattenTree(series ?? []).map(({ series: s, depth }) => (
              <option key={s.id} value={s.id}>
                {"  ".repeat(depth)}
                {seriesLabel(s, t)}
              </option>
            ))}
          </select>
          <button
            onClick={() => setEditing(false)}
            className="text-xs text-gray-400 hover:text-gray-700"
          >
            {t("series.cancel")}
          </button>
        </div>
      ) : trail.length > 0 ? (
        <nav aria-label={t("series.breadcrumb")} className="text-sm text-gray-700 flex flex-wrap items-center gap-1">
          {trail.map((s, i) => (
            <span key={s.id} className="flex items-center gap-1">
              {i > 0 && <span className="text-gray-300">›</span>}
              <span className={i === trail.length - 1 ? "font-medium" : "text-gray-500"}>{seriesLabel(s, t)}</span>
            </span>
          ))}
        </nav>
      ) : (
        <p className="text-sm text-gray-700">
          <span className="text-gray-400">{t("series.notAssigned")}</span>
        </p>
      )}
    </div>
  );
}
