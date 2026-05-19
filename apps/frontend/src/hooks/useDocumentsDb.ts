import { useQuery } from "@tanstack/react-query";
import {
  fetchDbDocumentDetail,
  fetchDbDocuments,
  fetchDbTypes,
  type DbListParams,
} from "@sdai/api-client";

export function useDbTypes() {
  return useQuery({
    queryKey: ["db-types"],
    queryFn:  fetchDbTypes,
    staleTime: 30_000,
  });
}

export function useDbDocuments(params: DbListParams) {
  return useQuery({
    queryKey: ["db-documents", params],
    queryFn:  () => fetchDbDocuments(params),
    placeholderData: (prev) => prev,
  });
}

export function useDbDocumentDetail(tableName: string, id: string) {
  return useQuery({
    queryKey: ["db-document", tableName, id],
    queryFn:  () => fetchDbDocumentDetail(tableName, id),
    enabled:  !!tableName && !!id,
  });
}
