import { useMutation, useQuery } from "@tanstack/react-query";
import {
  fetchBatchStatus,
  ingestFromPath,
  uploadFiles,
} from "@sdai/api-client";
import type { BatchStatus } from "@sdai/types";

const TERMINAL = new Set([
  "completed",
  "crashed",
  "out_of_scope",
  "review_required",
]);

function isDone(status: BatchStatus): boolean {
  return (
    status.documents.length > 0 &&
    status.documents.every((d) => TERMINAL.has(d.status))
  );
}

export function useUpload() {
  return useMutation({ mutationFn: (files: File[]) => uploadFiles(files) });
}

export function useIngestPath() {
  return useMutation({
    mutationFn: (payload: { path?: string; google_drive_folder_id?: string }) =>
      ingestFromPath(payload),
  });
}

export function useBatchStatus(batchId: string | null) {
  return useQuery({
    queryKey: ["ingest-status", batchId],
    queryFn: () => fetchBatchStatus(batchId!),
    enabled: !!batchId,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && isDone(data) ? false : 3000;
    },
  });
}
