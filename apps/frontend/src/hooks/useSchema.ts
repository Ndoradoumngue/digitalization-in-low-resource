import { useQuery } from "@tanstack/react-query";
import { fetchDbSchema } from "@sdai/api-client";

export function useDbSchema({ autoRefresh = false }: { autoRefresh?: boolean } = {}) {
  return useQuery({
    queryKey:        ["db-schema"],
    queryFn:         fetchDbSchema,
    staleTime:       5_000,
    refetchInterval: autoRefresh ? 10_000 : false,
  });
}
