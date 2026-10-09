import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchDocumentHistory,
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
