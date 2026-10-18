import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  extractDocument,
  extractDocuments,
  fetchDocumentHistory,
  fetchDocumentLocation,
  fetchDocumentVersions,
  restoreDocumentVersion,
  uploadDocumentVersion,
  type VersionChangeOptions,
} from "@sdai/api-client";

export function useDocumentVersions(tableName: string, id: string, enabled = true) {
  return useQuery({
    queryKey: ["document-versions", tableName, id],
    queryFn: () => fetchDocumentVersions(tableName, id),
    enabled: enabled && !!tableName && !!id,
  });
}

export function useDocumentHistory(tableName: string, id: string, enabled = true) {
  return useQuery({
    queryKey: ["document-history", tableName, id],
    queryFn: () => fetchDocumentHistory(tableName, id),
    enabled: enabled && !!tableName && !!id,
  });
}

// A new current version changes the document itself (current_version, and
// with re-extraction its fields and pages once the pipeline is done).
function invalidateDocument(queryClient: ReturnType<typeof useQueryClient>, tableName: string, id: string) {
  queryClient.invalidateQueries({ queryKey: ["document-versions", tableName, id] });
  queryClient.invalidateQueries({ queryKey: ["document-history", tableName, id] });
  queryClient.invalidateQueries({ queryKey: ["db-document", tableName, id] });
  queryClient.invalidateQueries({ queryKey: ["db-documents"] });
}

export function useUploadVersion(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, ...options }: { file: File } & VersionChangeOptions) =>
      uploadDocumentVersion(tableName, id, file, options),
    onSuccess: () => invalidateDocument(queryClient, tableName, id),
  });
}

export function useRestoreVersion(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ versionNo, ...options }: { versionNo: number } & VersionChangeOptions) =>
      restoreDocumentVersion(tableName, id, versionNo, options),
    onSuccess: () => invalidateDocument(queryClient, tableName, id),
  });
}

// ── Extraction on demand (GED Phase 10) ──────────────────────────────────────

export function useExtractDocument(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => extractDocument(tableName, id),
    onSuccess: () => invalidateDocument(queryClient, tableName, id),
  });
}

export function useExtractDocuments() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documents: { table_name: string; id: string }[]) => extractDocuments(documents),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["db-documents"] }),
  });
}

/** Where a document is filed now - followed when a first extraction moved it
 * into its detected type while it was open. */
export function useDocumentLocation(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["document-location", id],
    queryFn: () => fetchDocumentLocation(id),
    enabled: enabled && !!id,
    retry: false,
  });
}
