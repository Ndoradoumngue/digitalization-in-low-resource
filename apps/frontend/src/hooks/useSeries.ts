import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assignDocumentSeries,
  createSeries,
  deleteSeries,
  fetchFilingRules,
  fetchSeries,
  setFilingRule,
  updateSeries,
  type SeriesInput,
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
    mutationFn: ({ name, description, ...extra }: SeriesInput) =>
      createSeries(name, description ?? undefined, extra),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["series"] });
    },
  });
}

/** Rename / recode / move - moving can change who sees the documents below. */
export function useUpdateSeries() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Partial<SeriesInput> }) => updateSeries(id, changes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["series"] });
      queryClient.invalidateQueries({ queryKey: ["db-documents"] });
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
      queryClient.invalidateQueries({ queryKey: ["series"] });
    },
  });
}

export function useFilingRules(enabled = true) {
  return useQuery({ queryKey: ["filing-rules"], queryFn: fetchFilingRules, enabled, staleTime: 30_000 });
}

export function useSetFilingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableName, seriesId }: { tableName: string; seriesId: string | null }) =>
      setFilingRule(tableName, seriesId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["filing-rules"] });
    },
  });
}
