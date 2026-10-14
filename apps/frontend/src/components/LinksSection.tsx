import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import type { DocumentLink, LinkSuggestion } from "@sdai/types";
import {
  useCreateDocumentLink,
  useDocumentLinks,
  useDeleteDocumentLink,
} from "../hooks/useDocumentLinks";
import { useLinkSuggestions } from "../hooks/useTags";
import DocumentLinkPicker from "./DocumentLinkPicker";

function LinkRow({
  link,
  onRemove,
  removing,
  basePath,
  canEdit,
}: {
  link: DocumentLink;
  onRemove: () => void;
  removing: boolean;
  basePath: string;
  canEdit: boolean;
}) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const title = link.other_display ?? link.other_document_type ?? t("links.untitledDocument");
  // A destroyed document keeps its tombstone (record number, type) - the
  // link stays as the trace, but there is nothing left to open.
  const destroyed = !!link.other_destroyed;
  const inert = link.other_missing || destroyed;

  return (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-gray-100 last:border-0">
      <button
        onClick={() =>
          !inert && navigate(`${basePath}/${link.other_table}/${link.other_id}`)
        }
        disabled={inert}
        className={`flex-1 min-w-0 text-left ${inert ? "cursor-default" : "hover:underline"}`}
      >
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-indigo-100 text-indigo-800 mr-2">
          {link.relation}
        </span>
        <span className={`text-sm truncate ${inert ? "text-gray-400 italic" : "text-gray-800"}`}>
          {link.other_missing
            ? t("links.documentUnavailable")
            : destroyed
              ? link.other_record_id ?? t("links.untitledDocument")
              : title}
        </span>
        {link.other_document_type && !link.other_missing && (
          <span className="text-xs text-gray-400 ml-2">{link.other_document_type}</span>
        )}
        {destroyed && !link.other_missing && (
          <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-800 text-white">
            {t("lifecycle.status.destroyed")}
          </span>
        )}
      </button>
      {canEdit && (
        <button
          onClick={onRemove}
          disabled={removing}
          className="flex-shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
          title={t("links.removeLink")}
          aria-label={t("links.removeLink")}
        >
          ✕
        </button>
      )}
    </div>
  );
}

/** Documents this one cites (or that cite it) by reference number - one
 * click links them with a free-text relation (pre-filled). */
function SuggestionsPanel({ tableName, id, basePath }: { tableName: string; id: string; basePath: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: suggestions, isLoading } = useLinkSuggestions(tableName, id);
  const createMutation = useCreateDocumentLink(tableName, id);
  const queryClient = useQueryClient();
  const [relation, setRelation] = useState(t("links.suggestedRelation"));

  if (isLoading || !suggestions || suggestions.length === 0) return null;

  function link(s: LinkSuggestion) {
    createMutation.mutate(
      { to_table: s.table_name, to_id: s.id, relation: relation.trim() || t("links.suggestedRelation") },
      { onSuccess: () => queryClient.invalidateQueries({ queryKey: ["link-suggestions", tableName, id] }) },
    );
  }

  return (
    <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[11px] font-semibold text-amber-800">{t("links.suggestions")}</span>
        <input
          value={relation}
          onChange={(e) => setRelation(e.target.value)}
          className="h-6 w-32 rounded border border-amber-300 bg-white px-1 text-[11px]"
          aria-label={t("links.relationLabel")}
        />
      </div>
      {suggestions.map((s) => (
        <div key={`${s.table_name}-${s.id}`} className="flex items-center gap-2 py-1 text-xs">
          <button
            onClick={() => navigate(`${basePath}/${s.table_name}/${s.id}`)}
            className="flex-1 min-w-0 text-left hover:underline truncate"
          >
            <span className="font-medium text-gray-800">{s.label ?? s.record_id ?? s.document_type}</span>
            <span className="text-gray-500">
              {" "}
              · {t(s.direction === "mentions" ? "links.mentions" : "links.mentionedBy", { value: s.matched })}
            </span>
          </button>
          <button
            onClick={() => link(s)}
            disabled={createMutation.isPending}
            className="flex-shrink-0 px-2 py-0.5 rounded bg-amber-600 text-white text-[11px] font-semibold disabled:opacity-40"
          >
            {t("links.linkIt")}
          </button>
        </div>
      ))}
      {createMutation.error && <p className="text-[11px] text-red-600">{(createMutation.error as Error).message}</p>}
    </div>
  );
}

export default function LinksSection({
  tableName,
  id,
  basePath = "/documents",
  canEdit = true,
}: {
  tableName: string;
  id: string;
  basePath?: string;
  // link/unlink needs edit permission on this document (backend-enforced)
  canEdit?: boolean;
}) {
  const { t } = useTranslation();
  const { data, isLoading } = useDocumentLinks(tableName, id);
  const deleteMutation = useDeleteDocumentLink(tableName, id);
  const [showPicker, setShowPicker] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const links = data?.links ?? [];
  const outgoing = links.filter((l) => l.direction === "outgoing");
  const incoming = links.filter((l) => l.direction === "incoming");

  function remove(linkId: string) {
    setRemovingId(linkId);
    deleteMutation.mutate(linkId, { onSettled: () => setRemovingId(null) });
  }

  return (
    <div className="pt-4 mt-4 border-t border-gray-200">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          {t("links.heading")}
        </h3>
        {canEdit && (
          <button
            onClick={() => setShowPicker((s) => !s)}
            className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            {showPicker ? t("links.cancel") : t("links.addLink")}
          </button>
        )}
      </div>

      {showPicker && (
        <DocumentLinkPicker
          tableName={tableName}
          id={id}
          onDone={() => setShowPicker(false)}
        />
      )}

      {canEdit && <SuggestionsPanel tableName={tableName} id={id} basePath={basePath} />}

      {isLoading ? (
        <p className="text-sm text-gray-400">{t("links.loading")}</p>
      ) : links.length === 0 ? (
        <p className="text-sm text-gray-400">{t("links.noLinks")}</p>
      ) : (
        <div className="space-y-3">
          {outgoing.length > 0 && (
            <div>
              <div className="text-[11px] font-medium text-gray-400 mb-1">{t("links.relatesTo")}</div>
              {outgoing.map((l) => (
                <LinkRow canEdit={canEdit} key={l.id} link={l} onRemove={() => remove(l.id)} removing={removingId === l.id} basePath={basePath} />
              ))}
            </div>
          )}
          {incoming.length > 0 && (
            <div>
              <div className="text-[11px] font-medium text-gray-400 mb-1">{t("links.referencedBy")}</div>
              {incoming.map((l) => (
                <LinkRow canEdit={canEdit} key={l.id} link={l} onRemove={() => remove(l.id)} removing={removingId === l.id} basePath={basePath} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
