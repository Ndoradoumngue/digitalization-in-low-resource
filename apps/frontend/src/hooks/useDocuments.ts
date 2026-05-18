import { useQuery } from "@tanstack/react-query";
import { fetchDocuments, fetchOcrResult } from "@sdai/api-client";

export function useDocuments() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    staleTime: 30_000,
  });
}

export function useOcrResult(filename: string | null) {
  return useQuery({
    queryKey: ["ocrResult", filename],
    queryFn: () => fetchOcrResult(filename!),
    enabled: filename !== null,
    staleTime: 60_000,
  });
}
