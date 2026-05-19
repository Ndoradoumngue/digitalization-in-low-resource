import { useQuery } from "@tanstack/react-query";
import { fetchAuditLog, type AuditLogParams } from "@sdai/api-client";

export function useAuditLog(params: AuditLogParams = {}) {
  return useQuery({
    queryKey: ["audit-log", params],
    queryFn:  () => fetchAuditLog(params),
    staleTime: 30_000,
  });
}
