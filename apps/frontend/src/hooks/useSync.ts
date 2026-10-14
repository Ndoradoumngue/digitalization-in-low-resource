import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  discardEmails,
  fetchEmailOutbox,
  retryEmails,
  fetchSyncConflicts,
  fetchSyncProblemFiles,
  fetchSyncStatus,
  resolveSyncConflict,
  retrySyncFiles,
} from "@sdai/api-client";
import type { SyncResolution } from "@sdai/types";

/** Node sync/backup status - also feeds the sidebar alert, so it refreshes
 * every minute while any admin page is open. */
export function useSyncStatus(enabled = true) {
  return useQuery({
    queryKey: ["sync-status"],
    queryFn: fetchSyncStatus,
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useSyncConflicts(status: "open" | "resolved") {
  return useQuery({
    queryKey: ["sync-conflicts", status],
    queryFn: () => fetchSyncConflicts(status),
    staleTime: 15_000,
  });
}

export function useResolveSyncConflict() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, value }: { id: string; action: SyncResolution; value?: unknown }) =>
      resolveSyncConflict(id, action, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync-conflicts"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      queryClient.invalidateQueries({ queryKey: ["review-count"] });
    },
  });
}

export function useSyncProblemFiles() {
  return useQuery({ queryKey: ["sync-files"], queryFn: fetchSyncProblemFiles, staleTime: 15_000 });
}

export function useRetrySyncFiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: retrySyncFiles,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync-files"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
    },
  });
}

/** This server's e-mail outbox (GED Phase 7) - failed and retrying e-mails. */
export function useEmailOutbox() {
  return useQuery({ queryKey: ["email-outbox"], queryFn: () => fetchEmailOutbox(), refetchInterval: 60_000 });
}

export function useEmailOutboxAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, ids }: { kind: "retry" | "discard"; ids?: string[] }): Promise<unknown> =>
      kind === "retry" ? retryEmails(ids) : discardEmails(ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["email-outbox"] });
      queryClient.invalidateQueries({ queryKey: ["sync-status"] });
    },
  });
}
