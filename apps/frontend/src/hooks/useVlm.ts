import { useQuery } from "@tanstack/react-query";
import { fetchVlmResult, fetchVlmDocuments } from "@sdai/api-client";

export function useVlmDocuments() {
  return useQuery({
    queryKey: ["vlmDocuments"],
    queryFn: fetchVlmDocuments,
    staleTime: 30_000,
  });
}

export function useVlmResult(filename: string | null) {
  return useQuery({
    queryKey: ["vlmResult", filename],
    queryFn: () => fetchVlmResult(filename!),
    enabled: filename !== null,
    staleTime: 60_000,
  });
}
