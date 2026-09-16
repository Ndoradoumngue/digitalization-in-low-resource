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
  updateUserAccessManager,
  updateUserExtractionEditor,
} from "@sdai/api-client";

export function useGroups() {
  return useQuery({
    queryKey: ["admin-groups"],
    queryFn:  fetchGroups,
    staleTime: 30_000,
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
    queryFn:  fetchAdminUsers,
    staleTime: 30_000,
  });
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
  return useMutation({
    mutationFn: runIntegrityCheck,
  });
}

export function useExportArchive() {
  return useMutation({
    mutationFn: (format: "json" | "sql") => exportArchive(format),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      // The anchor must be attached to the DOM for .click() to reliably
      // trigger a download in every browser (Safari in particular ignores
      // clicks on detached elements). Revoking the object URL must also
      // be deferred — doing it synchronously right after click() can race
      // with the browser actually starting the download and silently
      // kill it before any bytes are read.
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
}
