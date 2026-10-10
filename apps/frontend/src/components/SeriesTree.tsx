import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { buildSeriesTree, subtreeIds, type Series, type SeriesNode } from "@sdai/types";
import { useAuth } from "../context/AuthContext";
import { useCreateSeries, useDeleteSeries, useSeries, useUpdateSeries } from "../hooks/useSeries";
import { flattenTree, seriesLabel, subtreeCount } from "../utils/series";

/** The plan de classement as a tree: pick a series to filter the document
 * list (optionally with its sub-series); admins also create, rename and
 * move series here. */
export default function SeriesTree({
  selectedId,
  includeSubseries,
  onSelect,
  onIncludeSubseriesChange,
}: {
  selectedId: string;
  includeSubseries: boolean;
  onSelect: (id: string) => void;
  onIncludeSubseriesChange: (value: boolean) => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { data: series } = useSeries();
  const tree = useMemo(() => buildSeriesTree(series ?? []), [series]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<string | null>(null); // parent id, "" for a root
  const createMutation = useCreateSeries();

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <aside className="w-72 flex-shrink-0 border-r border-gray-200 bg-white overflow-y-auto p-3 text-sm">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{t("series.planHeading")}</h2>
        {isAdmin && (
          <button onClick={() => setAdding("")} className="text-xs text-indigo-600 hover:underline">
            {t("series.addRoot")}
          </button>
        )}
      </div>
      <button
        onClick={() => onSelect("")}
        className={`w-full text-left px-2 py-1 rounded ${selectedId === "" ? "bg-indigo-50 text-indigo-700 font-semibold" : "hover:bg-gray-50"}`}
      >
        {t("series.allDocuments")}
      </button>
      {adding === "" && (
        <NameForm
          onCancel={() => setAdding(null)}
          onSave={(name, code) =>
            createMutation.mutate({ name, code: code || null }, { onSuccess: () => setAdding(null) })
          }
          error={createMutation.error as Error | null}
        />
      )}
      <ul className="mt-1">
        {tree.map((node) => (
          <TreeNode
            key={node.id}
            node={node}
            depth={0}
            all={series ?? []}
            open={open}
            toggle={toggle}
            selectedId={selectedId}
            onSelect={onSelect}
            isAdmin={isAdmin}
            adding={adding}
            setAdding={setAdding}
          />
        ))}
      </ul>
      {(series ?? []).length === 0 && <p className="text-xs text-gray-400 mt-2">{t("series.noneYet")}</p>}
      <label className="mt-3 flex items-center gap-2 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={includeSubseries}
          onChange={(e) => onIncludeSubseriesChange(e.target.checked)}
        />
        {t("series.includeSubseries")}
      </label>
    </aside>
  );
}

function TreeNode({
  node,
  depth,
  all,
  open,
  toggle,
  selectedId,
  onSelect,
  isAdmin,
  adding,
  setAdding,
}: {
  node: SeriesNode;
  depth: number;
  all: Series[];
  open: Set<string>;
  toggle: (id: string) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  isAdmin: boolean;
  adding: string | null;
  setAdding: (id: string | null) => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"idle" | "rename" | "move">("idle");
  const createMutation = useCreateSeries();
  const updateMutation = useUpdateSeries();
  const deleteMutation = useDeleteSeries();
  const expanded = open.has(node.id);
  const selected = selectedId === node.id;
  const error = (updateMutation.error ?? deleteMutation.error ?? createMutation.error) as Error | null;
  // A series can't move under itself or its own sub-series.
  const blocked = subtreeIds(all, node.id);

  return (
    <li>
      <div className={`group flex items-center gap-1 rounded ${selected ? "bg-indigo-50" : "hover:bg-gray-50"}`} style={{ paddingLeft: depth * 12 }}>
        <button
          onClick={() => toggle(node.id)}
          className={`w-4 text-gray-400 ${node.children.length ? "" : "invisible"}`}
          aria-label={expanded ? t("series.collapse") : t("series.expand")}
        >
          {expanded ? "▾" : "▸"}
        </button>
        <button
          onClick={() => onSelect(node.id)}
          className={`flex-1 min-w-0 text-left py-1 truncate ${selected ? "text-indigo-700 font-semibold" : "text-gray-700"}`}
          title={node.description ?? undefined}
        >
          {seriesLabel(node, t)}
        </button>
        <span className="text-[10px] text-gray-400 pr-1">{subtreeCount(node)}</span>
        {isAdmin && (
          <span className="hidden group-hover:flex gap-1 pr-1 text-[11px]">
            <button onClick={() => setAdding(node.id)} title={t("series.addChild")} className="text-indigo-600">＋</button>
            <button onClick={() => setMode("rename")} title={t("series.rename")} className="text-gray-500">✎</button>
            <button onClick={() => setMode("move")} title={t("series.move")} className="text-gray-500">⇄</button>
            <button
              onClick={() => deleteMutation.mutate(node.id)}
              title={t("series.delete")}
              className="text-gray-400 hover:text-red-600"
            >
              ✕
            </button>
          </span>
        )}
      </div>
      {mode === "rename" && (
        <div style={{ paddingLeft: depth * 12 + 16 }}>
          <NameForm
            initialName={node.name}
            initialCode={node.code ?? ""}
            onCancel={() => setMode("idle")}
            onSave={(name, code) =>
              updateMutation.mutate(
                { id: node.id, changes: { name, code: code || null } },
                { onSuccess: () => setMode("idle") },
              )
            }
            error={null}
          />
        </div>
      )}
      {mode === "move" && (
        <div className="flex items-center gap-1 py-1" style={{ paddingLeft: depth * 12 + 16 }}>
          <select
            autoFocus
            defaultValue={node.parent_id ?? ""}
            onChange={(e) =>
              updateMutation.mutate(
                { id: node.id, changes: { parent_id: e.target.value || null } },
                { onSuccess: () => setMode("idle") },
              )
            }
            className="h-7 flex-1 rounded border border-gray-300 text-xs"
            aria-label={t("series.moveTo")}
          >
            <option value="">{t("series.root")}</option>
            {flattenTree(all)
              .filter(({ series }) => !blocked.has(series.id))
              .map(({ series, depth: d }) => (
                <option key={series.id} value={series.id}>
                  {"  ".repeat(d)}
                  {seriesLabel(series, t)}
                </option>
              ))}
          </select>
          <button onClick={() => setMode("idle")} className="text-xs text-gray-400">
            {t("common.cancel")}
          </button>
        </div>
      )}
      {adding === node.id && (
        <div style={{ paddingLeft: depth * 12 + 16 }}>
          <NameForm
            onCancel={() => setAdding(null)}
            onSave={(name, code) =>
              createMutation.mutate(
                { name, code: code || null, parent_id: node.id },
                {
                  onSuccess: () => {
                    setAdding(null);
                    if (!expanded) toggle(node.id);
                  },
                },
              )
            }
            error={null}
          />
        </div>
      )}
      {error && <p className="text-[11px] text-red-600 px-2" style={{ paddingLeft: depth * 12 + 16 }}>{error.message}</p>}
      {expanded && node.children.length > 0 && (
        <ul>
          {node.children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              all={all}
              open={open}
              toggle={toggle}
              selectedId={selectedId}
              onSelect={onSelect}
              isAdmin={isAdmin}
              adding={adding}
              setAdding={setAdding}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function NameForm({
  initialName = "",
  initialCode = "",
  onSave,
  onCancel,
  error,
}: {
  initialName?: string;
  initialCode?: string;
  onSave: (name: string, code: string) => void;
  onCancel: () => void;
  error: Error | null;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const [code, setCode] = useState(initialCode);
  return (
    <form
      className="flex items-center gap-1 py-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave(name.trim(), code.trim());
      }}
    >
      <input
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder={t("series.codePlaceholder")}
        className="h-7 w-14 rounded border border-gray-300 px-1 text-xs"
        aria-label={t("series.code")}
      />
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={t("series.namePlaceholder")}
        className="h-7 flex-1 rounded border border-gray-300 px-1 text-xs"
        aria-label={t("series.name")}
      />
      <button type="submit" className="text-xs text-indigo-600">{t("common.save")}</button>
      <button type="button" onClick={onCancel} className="text-xs text-gray-400">✕</button>
      {error && <span className="text-[11px] text-red-600">{error.message}</span>}
    </form>
  );
}
