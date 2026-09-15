import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addUserToGroup,
  createGroup,
  deleteGroup,
  fetchAdminUsers,
  fetchGroups,
  removeUserFromGroup,
  runIntegrityCheck,
  updateUserAccessManager,
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
