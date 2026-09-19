import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { DocumentLink } from "@sdai/types";
import { useDocumentLinks, useDeleteDocumentLink } from "../hooks/useDocumentLinks";
import DocumentLinkPicker from "./DocumentLinkPicker";

function LinkRow({
  link,
  onRemove,
  removing,
  basePath,
}: {
  link: DocumentLink;
  onRemove: () => void;
  removing: boolean;
  basePath: string;
}) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const title = link.other_display ?? link.other_document_type ?? t("links.untitledDocument");

  return (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-gray-100 last:border-0">
      <button
        onClick={() =>
          !link.other_missing && navigate(`${basePath}/${link.other_table}/${link.other_id}`)
        }
        disabled={link.other_missing}
        className={`flex-1 min-w-0 text-left ${
          link.other_missing ? "cursor-default" : "hover:underline"
        }`}
      >
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-indigo-100 text-indigo-800 mr-2">
          {link.relation}
        </span>
        <span className={`text-sm truncate ${link.other_missing ? "text-gray-400 italic" : "text-gray-800"}`}>
          {link.other_missing ? t("links.documentUnavailable") : title}
        </span>
        {link.other_document_type && !link.other_missing && (
          <span className="text-xs text-gray-400 ml-2">{link.other_document_type}</span>
        )}
      </button>
      <button
        onClick={onRemove}
        disabled={removing}
        className="flex-shrink-0 text-xs text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
        title={t("links.removeLink")}
        aria-label={t("links.removeLink")}
      >
        ✕
      </button>
    </div>
  );
}

export default function LinksSection({
  tableName,
  id,
  basePath = "/documents",
}: {
  tableName: string;
  id: string;
  basePath?: string;
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
        <button
          onClick={() => setShowPicker((s) => !s)}
          className="text-xs font-medium text-indigo-600 hover:text-indigo-800 transition-colors"
        >
          {showPicker ? t("links.cancel") : t("links.addLink")}
        </button>
      </div>

      {showPicker && (
        <DocumentLinkPicker
          tableName={tableName}
          id={id}
          onDone={() => setShowPicker(false)}
        />
      )}

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
                <LinkRow key={l.id} link={l} onRemove={() => remove(l.id)} removing={removingId === l.id} basePath={basePath} />
              ))}
            </div>
          )}
          {incoming.length > 0 && (
            <div>
              <div className="text-[11px] font-medium text-gray-400 mb-1">{t("links.referencedBy")}</div>
              {incoming.map((l) => (
                <LinkRow key={l.id} link={l} onRemove={() => remove(l.id)} removing={removingId === l.id} basePath={basePath} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
