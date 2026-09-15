import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocumentLink,
  deleteDocumentLink,
  fetchDocumentLinks,
  type CreateLinkBody,
} from "@sdai/api-client";

export function useDocumentLinks(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-links", tableName, id],
    queryFn:  () => fetchDocumentLinks(tableName, id),
    enabled:  !!tableName && !!id,
  });
}

export function useCreateDocumentLink(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateLinkBody) => createDocumentLink(tableName, id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-links", tableName, id] });
    },
  });
}

export function useDeleteDocumentLink(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (linkId: string) => deleteDocumentLink(linkId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document-links", tableName, id] });
    },
  });
}
