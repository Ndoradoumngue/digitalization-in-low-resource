import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createDocumentAccess,
  createScopedGrant,
  deleteDocumentAccess,
  fetchDocumentAccess,
  fetchGrantees,
  fetchScopedGrants,
  setDocumentConfidentiality,
  updateAccessGrant,
  type CreateAccessGrantBody,
  type CreateScopedGrantBody,
} from "@sdai/api-client";
import type { Confidentiality, PermissionLevel } from "@sdai/types";
import { useAuth } from "../context/AuthContext";
import { atLeast, canEditExtraction, isActor } from "../utils/permissions";

export function useGrantees() {
  return useQuery({
    queryKey: ["grantees"],
    queryFn:  fetchGrantees,
    staleTime: 30_000,
  });
}

export function useDocumentAccess(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-access", tableName, id],
    queryFn:  () => fetchDocumentAccess(tableName, id),
    enabled:  !!tableName && !!id,
  });
}

export function useCreateDocumentAccess(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAccessGrantBody) => createDocumentAccess(tableName, id, body),
    onSuccess: () => invalidateVisibility(queryClient),
  });
}

/** What the signed-in user may do on one document - their own level on
 * it (from the access endpoint) combined with their role and flags. */
export function useDocumentPermissions(tableName: string, id: string) {
  const { user } = useAuth();
  const { data } = useDocumentAccess(tableName, id);
  const level = data?.my_permission ?? null;
  return {
    level,
    canDownload: atLeast(level, "download"),
    canAct: atLeast(level, "edit") && isActor(user),
    canEditFields: atLeast(level, "edit") && canEditExtraction(user),
    canManageAccess: data?.can_manage ?? false,
  };
}

// A grant or confidentiality change can change what anyone sees - refetch
// every document listing, not just this document's access panel.
function invalidateVisibility(queryClient: ReturnType<typeof useQueryClient>) {
  for (const key of ["document-access", "db-documents", "db-document", "db-types", "review-queue", "review-count", "scoped-grants"]) {
    queryClient.invalidateQueries({ queryKey: [key] });
  }
}

export function useUpdateAccessGrant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ grantId, permission }: { grantId: string; permission: PermissionLevel }) =>
      updateAccessGrant(grantId, permission),
    onSuccess: () => invalidateVisibility(queryClient),
  });
}

export function useScopedGrants(scope: "series" | "doc_type") {
  return useQuery({
    queryKey: ["scoped-grants", scope],
    queryFn: () => fetchScopedGrants(scope),
  });
}

export function useCreateScopedGrant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateScopedGrantBody) => createScopedGrant(body),
    onSuccess: () => invalidateVisibility(queryClient),
  });
}

export function useDeleteGrant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (grantId: string) => deleteDocumentAccess(grantId),
    onSuccess: () => invalidateVisibility(queryClient),
  });
}

export function useSetConfidentiality(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (level: Confidentiality) => setDocumentConfidentiality(tableName, id, level),
    onSuccess: () => invalidateVisibility(queryClient),
  });
}
