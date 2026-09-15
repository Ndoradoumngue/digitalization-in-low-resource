import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assignDocumentSeries,
  createSeries,
  deleteSeries,
  fetchSeries,
} from "@sdai/api-client";

export function useSeries() {
  return useQuery({
    queryKey: ["series"],
    queryFn:  fetchSeries,
    staleTime: 30_000,
  });
}

export function useCreateSeries() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      createSeries(name, description),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["series"] });
    },
  });
}

export function useDeleteSeries() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (seriesId: string) => deleteSeries(seriesId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["series"] });
    },
  });
}

export function useAssignDocumentSeries(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (seriesId: string | null) => assignDocumentSeries(tableName, id, seriesId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["db-document", tableName, id] });
      queryClient.invalidateQueries({ queryKey: ["db-documents"] });
    },
  });
}
