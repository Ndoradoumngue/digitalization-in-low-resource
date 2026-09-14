import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteIngestedDocument,
  fetchBatchStatus,
  fetchPageStatus,
  fetchRecentBatches,
  ingestFromPath,
  manualEnterPage,
  reloadPage,
  resumeDocument,
  retryPage,
  skipPage,
  uploadFiles,
} from "@sdai/api-client";
import type { BatchStatus, BatchSummary, PageList } from "@sdai/types";
import { isAuthError, isClientError } from "./queryUtils";

const TERMINAL = new Set([
  "completed",
  "crashed",
  "out_of_scope",
  "review_required",
  "duplicate",
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
    retry: (failureCount, error) => !isClientError(error) && failureCount < 3,
    refetchInterval: (query) => {
      if (isClientError(query.state.error)) return false;
      const data = query.state.data;
      return data && isDone(data) ? false : 3000;
    },
  });
}

function isSummaryDone(batches: BatchSummary[]): boolean {
  return batches.every((b) => b.pending === 0);
}

// Recent ingestion batches, so an in-progress or completed batch can be
// found again after a page refresh instead of only living in local state.
export function useRecentBatches(limit = 20) {
  return useQuery({
    queryKey: ["ingest-batches", limit],
    queryFn: () => fetchRecentBatches(limit),
    retry: (failureCount, error) => !isClientError(error) && failureCount < 3,
    refetchInterval: (query) => {
      if (isClientError(query.state.error)) return false;
      const data = query.state.data;
      return data && isSummaryDone(data) ? false : 5000;
    },
  });
}

function isPageListDone(list: PageList): boolean {
  return list.pending === 0 && list.processing === 0;
}

// Per-page progress for one document — powers the page-level progress bar
// and failed-pages list on multi-page documents.
export function usePageStatus(batchDocumentId: string | null) {
  return useQuery({
    queryKey: ["ingest-pages", batchDocumentId],
    queryFn: () => fetchPageStatus(batchDocumentId!),
    enabled: !!batchDocumentId,
    // A 404 right after upload usually just means Stage 1 hasn't created
    // the page rows yet (a large PDF can take a while to preprocess) —
    // keep retrying rather than giving up after one check, which
    // previously required a manual page refresh to recover. Only stop
    // outright on an auth failure, which won't resolve by retrying.
    retry: (failureCount, error) => !isAuthError(error) && failureCount < 40,
    retryDelay: 3000,
    refetchInterval: (query) => {
      if (isAuthError(query.state.error)) return false;
      const data = query.state.data;
      return data && isPageListDone(data) ? false : 3000;
    },
  });
}

export function useRetryPage(batchDocumentId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pageId: string) => retryPage(pageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingest-pages", batchDocumentId] });
    },
  });
}

// Re-derives a page from its original source document (re-running PDF
// extraction + preprocessing, including current column-split detection)
// instead of reusing the already-preprocessed stored image.
export function useReloadPage(batchDocumentId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pageId: string) => reloadPage(pageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingest-pages", batchDocumentId] });
    },
  });
}

export function useManualEnterPage(batchDocumentId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, fields }: { pageId: string; fields: Record<string, unknown> }) =>
      manualEnterPage(pageId, fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingest-pages", batchDocumentId] });
    },
  });
}

export function useSkipPage(batchDocumentId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pageId: string) => skipPage(pageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingest-pages", batchDocumentId] });
    },
  });
}

// Resume every pending/failed page of a document — e.g. after an
// interrupted run. Processing happens in the background; invalidating
// here just lets the existing per-page polling pick up the "processing"
// transition immediately instead of waiting for its next tick.
export function useResumeDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batchDocumentId: string) => resumeDocument(batchDocumentId),
    onSuccess: (_data, batchDocumentId) => {
      queryClient.invalidateQueries({ queryKey: ["ingest-pages", batchDocumentId] });
    },
  });
}

// Delete the extracted document for one upload, so the same file can be
// re-uploaded without tripping the content-hash duplicate check.
export function useDeleteIngestedDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batchDocumentId: string) => deleteIngestedDocument(batchDocumentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ingest-batches"] });
      queryClient.invalidateQueries({ queryKey: ["ingest-status"] });
    },
  });
}
