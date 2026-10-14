import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  archiveDocument,
  createLegalHold,
  createRetentionRule,
  decideDestruction,
  deleteRetentionRule,
  extendRetention,
  fetchDestruction,
  fetchDestructions,
  fetchDocumentLifecycle,
  fetchEligibleDocuments,
  fetchLegalHolds,
  fetchRetentionRules,
  fetchTrash,
  proposeDestruction,
  releaseLegalHold,
  restoreDocument,
  runRetentionJob,
  trashDocument,
  updateRetentionRule,
} from "@sdai/api-client";
import type { LegalHoldBody, RetentionRuleBody } from "@sdai/types";

// Everything a lifecycle change can make stale: the document itself, lists,
// counts, the trash, the destruction views.
function invalidateLifecycle(queryClient: QueryClient) {
  for (const key of [
    "document-lifecycle",
    "trash",
    "retention-rules",
    "legal-holds",
    "destruction-eligible",
    "destructions",
    "destruction",
    "db-documents",
    "db-document",
    "db-types",
    "document-history",
    "document-links",
    "document-access",
    "series",
    "review-queue",
    "review-count",
  ]) {
    queryClient.invalidateQueries({ queryKey: [key] });
  }
}

export function useDocumentLifecycle(tableName: string, id: string, enabled = true) {
  return useQuery({
    queryKey: ["document-lifecycle", tableName, id],
    queryFn: () => fetchDocumentLifecycle(tableName, id),
    enabled: enabled && !!tableName && !!id,
  });
}

export function useLifecycleAction(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      action:
        | { kind: "archive" }
        | { kind: "trash"; reason?: string }
        | { kind: "restore" }
        | { kind: "extend"; until: string; reason?: string },
    ) => {
      switch (action.kind) {
        case "archive":
          return archiveDocument(tableName, id);
        case "trash":
          return trashDocument(tableName, id, action.reason);
        case "restore":
          return restoreDocument(tableName, id);
        case "extend":
          return extendRetention(tableName, id, action.until, action.reason);
      }
    },
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useRestoreFromTrash() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableName, id }: { tableName: string; id: string }) => restoreDocument(tableName, id),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useTrash(page = 1, enabled = true) {
  return useQuery({ queryKey: ["trash", page], queryFn: () => fetchTrash(page), enabled });
}

// ── Retention rules ───────────────────────────────────────────────────────────

export function useRetentionRules(enabled = true) {
  return useQuery({ queryKey: ["retention-rules"], queryFn: fetchRetentionRules, enabled });
}

export function useSaveRetentionRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: RetentionRuleBody }) => {
      if (!id) return createRetentionRule(body);
      const { scope: _scope, series_id: _series, table_name: _table, ...changes } = body;
      return updateRetentionRule(id, changes);
    },
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useDeleteRetentionRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) => deleteRetentionRule(ruleId),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useRunRetentionJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runRetentionJob,
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

// ── Legal holds ───────────────────────────────────────────────────────────────

export function useLegalHolds(includeReleased = false, enabled = true) {
  return useQuery({
    queryKey: ["legal-holds", includeReleased],
    queryFn: () => fetchLegalHolds(includeReleased),
    enabled,
  });
}

export function useCreateLegalHold() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LegalHoldBody) => createLegalHold(body),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useReleaseLegalHold() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ holdId, reason }: { holdId: string; reason: string }) => releaseLegalHold(holdId, reason),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

// ── Destruction ───────────────────────────────────────────────────────────────

export function useEligibleDocuments(enabled = true) {
  return useQuery({ queryKey: ["destruction-eligible"], queryFn: fetchEligibleDocuments, enabled });
}

export function useDestructions(enabled = true) {
  return useQuery({ queryKey: ["destructions"], queryFn: fetchDestructions, enabled });
}

export function useDestruction(recordId: string | null) {
  return useQuery({
    queryKey: ["destruction", recordId],
    queryFn: () => fetchDestruction(recordId!),
    enabled: !!recordId,
  });
}

export function useProposeDestruction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ documents, comment }: { documents: { table_name: string; id: string }[]; comment?: string }) =>
      proposeDestruction(documents, comment),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}

export function useDecideDestruction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      recordId,
      decision,
      note,
    }: {
      recordId: string;
      decision: "approve" | "reject" | "cancel";
      note?: string;
    }) => decideDestruction(recordId, decision, note),
    onSuccess: () => invalidateLifecycle(queryClient),
  });
}
