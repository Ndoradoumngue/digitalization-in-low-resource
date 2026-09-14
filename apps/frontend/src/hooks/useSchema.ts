import { useQuery } from "@tanstack/react-query";
import { fetchDbSchema } from "@sdai/api-client";
import { isClientError } from "./queryUtils";

export function useDbSchema({ autoRefresh = false }: { autoRefresh?: boolean } = {}) {
  return useQuery({
    queryKey:        ["db-schema"],
    queryFn:         fetchDbSchema,
    staleTime:       5_000,
    retry:           (failureCount, error) => !isClientError(error) && failureCount < 3,
    refetchInterval: (query) => {
      if (!autoRefresh || isClientError(query.state.error)) return false;
      return 10_000;
    },
  });
}
