import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addUserToGroup,
  createGroup,
  deleteGroup,
  exportArchive,
  fetchAdminUsers,
  fetchGroups,
  removeUserFromGroup,
  runIntegrityCheck,
  fetchLastIntegrityRun,
  verifyAuditChain,
  updateAdminUser,
  createAdminUser,
  setUserActive,
  resetUserPassword,
  deleteAdminUser,
  type CreateUserBody,
  updateUserAccessManager,
  updateUserExtractionEditor,
  type UpdateUserBody,
} from "@sdai/api-client";
import { saveBlob } from "../utils/download";

export function useGroups(enabled = true) {
  return useQuery({
    queryKey: ["admin-groups"],
    queryFn: fetchGroups,
    staleTime: 30_000,
    enabled,
  });
}

export function useCreateGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => createGroup(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
    },
  });
}

export function useDeleteGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (groupId: string) => deleteGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

export function useAdminUsers() {
  return useQuery({
    queryKey: ["admin-users"],
    queryFn: fetchAdminUsers,
    staleTime: 30_000,
  });
}

/** Partial update of a user's role, delegated flags or clearance. */
export function useUpdateAdminUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: UpdateUserBody }) =>
      updateAdminUser(userId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

// ── Account management (GED Phase 9) ─────────────────────────────────────────

function useUserMutation<V>(fn: (vars: V) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
    },
  });
}

export function useCreateAdminUser() {
  return useUserMutation((body: CreateUserBody) => createAdminUser(body));
}

export function useSetUserActive() {
  return useUserMutation(({ userId, active }: { userId: string; active: boolean }) =>
    setUserActive(userId, active),
  );
}

export function useResetUserPassword() {
  return useUserMutation(({ userId, password }: { userId: string; password: string }) =>
    resetUserPassword(userId, password),
  );
}

export function useDeleteAdminUser() {
  return useUserMutation((userId: string) => deleteAdminUser(userId));
}

export function useUpdateUserAccessManager() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, canManageAccess }: { userId: string; canManageAccess: boolean }) =>
      updateUserAccessManager(userId, canManageAccess),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

export function useUpdateUserExtractionEditor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, canEditExtraction }: { userId: string; canEditExtraction: boolean }) =>
      updateUserExtractionEditor(userId, canEditExtraction),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
}

export function useAddUserToGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, groupId }: { userId: string; groupId: string }) =>
      addUserToGroup(userId, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
    },
  });
}

export function useRemoveUserFromGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, groupId }: { userId: string; groupId: string }) =>
      removeUserFromGroup(userId, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
    },
  });
}

export function useRunIntegrityCheck() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runIntegrityCheck,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["integrity-last-run"] }),
  });
}

export function useLastIntegrityRun() {
  return useQuery({
    queryKey: ["integrity-last-run"],
    queryFn: fetchLastIntegrityRun,
  });
}

export function useVerifyAuditChain() {
  return useMutation({ mutationFn: verifyAuditChain });
}

export function useExportArchive() {
  return useMutation({
    mutationFn: (format: "json" | "sql") => exportArchive(format),
    onSuccess: ({ blob, filename }) => saveBlob(blob, filename),
  });
}
