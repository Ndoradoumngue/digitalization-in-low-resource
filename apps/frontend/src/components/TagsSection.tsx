import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Tag } from "@sdai/types";
import { useAddDocumentTag, useDocumentTags, useRemoveDocumentTag, useTags } from "../hooks/useTags";

export function TagChip({ tag, onRemove }: { tag: Tag; onRemove?: () => void }) {
  const style = tag.color ? { backgroundColor: `${tag.color}22`, borderColor: tag.color, color: tag.color } : undefined;
  return (
    <span
      style={style}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border border-gray-300 bg-gray-50 text-gray-700"
    >
      {tag.name}
      {onRemove && (
        <button onClick={onRemove} className="opacity-60 hover:opacity-100" aria-label={`✕ ${tag.name}`}>
          ✕
        </button>
      )}
    </span>
  );
}

/** A document's tags - chips, plus an input with autocomplete over the
 * tenant's vocabulary (a new name creates the tag) for users who may edit. */
export default function TagsSection({ tableName, id, canEdit }: { tableName: string; id: string; canEdit: boolean }) {
  const { t } = useTranslation();
  const { data: tags } = useDocumentTags(tableName, id);
  const { data: vocabulary } = useTags();
  const addMutation = useAddDocumentTag(tableName, id);
  const removeMutation = useRemoveDocumentTag(tableName, id);
  const [value, setValue] = useState("");
  const listId = `tag-options-${id}`;
  const current = new Set((tags ?? []).map((tag) => tag.id));

  function add() {
    const name = value.trim();
    if (!name) return;
    const known = (vocabulary ?? []).find((tag) => tag.name.toLowerCase() === name.toLowerCase());
    addMutation.mutate(known ? { tagId: known.id } : { name }, { onSuccess: () => setValue("") });
  }

  return (
    <div className="pt-4 mt-4 border-t border-gray-200">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">{t("tags.heading")}</h3>
      <div className="flex flex-wrap gap-1.5">
        {(tags ?? []).map((tag) => (
          <TagChip key={tag.id} tag={tag} onRemove={canEdit ? () => removeMutation.mutate(tag.id) : undefined} />
        ))}
        {(tags ?? []).length === 0 && <span className="text-sm text-gray-400">{t("tags.none")}</span>}
      </div>
      {canEdit && (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <input
            list={listId}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={t("tags.addPlaceholder")}
            className="h-8 flex-1 rounded-md border border-gray-300 px-2 text-sm"
            aria-label={t("tags.addPlaceholder")}
          />
          <datalist id={listId}>
            {(vocabulary ?? [])
              .filter((tag) => !current.has(tag.id))
              .map((tag) => (
                <option key={tag.id} value={tag.name} />
              ))}
          </datalist>
          <button type="submit" disabled={addMutation.isPending} className="text-xs font-medium text-indigo-600">
            {t("tags.add")}
          </button>
        </form>
      )}
      {(addMutation.error || removeMutation.error) && (
        <p className="mt-1 text-xs text-red-600">{((addMutation.error ?? removeMutation.error) as Error).message}</p>
      )}
    </div>
  );
}
