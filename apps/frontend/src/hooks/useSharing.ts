import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocumentShare,
  createExport,
  deleteExportJob,
  downloadFile,
  fetchDocumentShares,
  fetchExportJobs,
  revokeDocumentShare,
  type CreateShareBody,
} from "@sdai/api-client";
import type { ExportRequest } from "@sdai/types";
import { saveBlob } from "../utils/download";

// ── Share links ───────────────────────────────────────────────────────────────

export function useDocumentShares(tableName: string, id: string, enabled = true) {
  return useQuery({
    queryKey: ["document-shares", tableName, id],
    queryFn: () => fetchDocumentShares(tableName, id),
    enabled: enabled && !!tableName && !!id,
  });
}

export function useCreateShare(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateShareBody) => createDocumentShare(tableName, id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-shares", tableName, id] });
      queryClient.invalidateQueries({ queryKey: ["document-history", tableName, id] });
    },
  });
}

export function useRevokeShare(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (shareId: string) => revokeDocumentShare(tableName, id, shareId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-shares", tableName, id] });
      queryClient.invalidateQueries({ queryKey: ["document-history", tableName, id] });
    },
  });
}

// ── Selective export ─────────────────────────────────────────────────────────

export function useExportJobs() {
  return useQuery({
    queryKey: ["export-jobs"],
    queryFn: fetchExportJobs,
    // Poll while a ZIP is being built.
    refetchInterval: (query) =>
      query.state.data?.jobs.some((j) => j.status === "queued" || j.status === "running") ? 2000 : false,
  });
}

export function useCreateExport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ExportRequest) => createExport(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["export-jobs"] }),
  });
}

export function useDeleteExportJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => deleteExportJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["export-jobs"] }),
  });
}

// ── File downloads (original, export ZIP) ────────────────────────────────────

export function useDownloadFile() {
  return useMutation({
    mutationFn: (url: string) => downloadFile(url),
    onSuccess: ({ blob, filename }) => saveBlob(blob, filename),
  });
}
