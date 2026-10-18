import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { fetchDocumentLocation } from "@sdai/api-client";
import { useBatchStatus } from "../hooks/useIngest";
import { useExtractDocument } from "../hooks/useVersions";

const FINISHED = new Set(["completed", "review_required", "crashed", "out_of_scope", "duplicate"]);

// "Not extracted" on the record tab of a document uploaded without
// extraction (GED Phase 10): one button runs the extraction, then the page
// follows the document into its detected type (its table changes once).
export default function ExtractionPanel({
  tableName,
  id,
  canExtract,
}: {
  tableName: string;
  id: string;
  canExtract: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const extract = useExtractDocument(tableName, id);
  const [batchId, setBatchId] = useState<string | null>(null);
  const { data: batch } = useBatchStatus(batchId);
  const outcome = batch?.documents[0]?.status;
  const finished = outcome !== undefined && FINISHED.has(outcome);

  useEffect(() => {
    if (!finished || outcome === "crashed") return;
    let cancelled = false;
    fetchDocumentLocation(id)
      .then(({ table_name }) => {
        if (cancelled) return;
        queryClient.invalidateQueries({ queryKey: ["db-documents"] });
        queryClient.invalidateQueries({ queryKey: ["db-document"] });
        navigate(`/documents/${table_name}/${id}`, { replace: true });
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [finished, outcome, id, navigate, queryClient]);

  const running = (batchId !== null && !finished) || extract.isPending;

  return (
    <section className="mb-6 rounded-lg border border-gray-200 bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-800">{t("extraction.notExtracted")}</h3>
          <p className="text-xs text-gray-500">
            {running ? t("extraction.running") : t("extraction.explanation")}
          </p>
        </div>
        {canExtract && (
          <button
            onClick={() => extract.mutate(undefined, { onSuccess: ({ batch_id }) => setBatchId(batch_id) })}
            disabled={running}
            className="flex-shrink-0 h-8 px-3 rounded-md text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
          >
            {running ? t("extraction.extracting") : t("extraction.extract")}
          </button>
        )}
      </div>
      {outcome === "crashed" && (
        <p role="alert" className="mt-2 text-xs text-red-600">{t("extraction.failed")}</p>
      )}
      {extract.isError && (
        <p role="alert" className="mt-2 text-xs text-red-600">{(extract.error as Error).message}</p>
      )}
    </section>
  );
}
