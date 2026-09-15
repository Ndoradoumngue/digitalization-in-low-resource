import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocumentAccess,
  deleteDocumentAccess,
  fetchDocumentAccess,
  type CreateAccessGrantBody,
} from "@sdai/api-client";

export function useDocumentAccess(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-access", tableName, id],
    queryFn:  () => fetchDocumentAccess(tableName, id),
    enabled:  !!tableName && !!id,
  });
}

export function useCreateDocumentAccess(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAccessGrantBody) => createDocumentAccess(tableName, id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-access", tableName, id] });
    },
  });
}

export function useDeleteDocumentAccess(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (grantId: string) => deleteDocumentAccess(grantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-access", tableName, id] });
    },
  });
}
