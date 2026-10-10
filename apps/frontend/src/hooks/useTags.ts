import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addDocumentTag,
  createTag,
  deleteTag,
  fetchDocumentTags,
  fetchLinkSuggestions,
  fetchTags,
  mergeTag,
  removeDocumentTag,
  updateTag,
} from "@sdai/api-client";

export function useTags() {
  return useQuery({ queryKey: ["tags"], queryFn: fetchTags, staleTime: 30_000 });
}

export function useDocumentTags(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-tags", tableName, id],
    queryFn: () => fetchDocumentTags(tableName, id),
    enabled: Boolean(tableName && id),
  });
}

function useTagMutation<T>(fn: (arg: T) => Promise<unknown>, documentKey?: [string, string]) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: documentKey ? ["document-tags", ...documentKey] : ["document-tags"] });
      queryClient.invalidateQueries({ queryKey: ["db-documents"] });
    },
  });
}

export function useAddDocumentTag(tableName: string, id: string) {
  return useTagMutation(
    (tag: { tagId?: string; name?: string }) => addDocumentTag(tableName, id, tag),
    [tableName, id],
  );
}

export function useRemoveDocumentTag(tableName: string, id: string) {
  return useTagMutation((tagId: string) => removeDocumentTag(tableName, id, tagId), [tableName, id]);
}

export function useCreateTag() {
  return useTagMutation(({ name, color }: { name: string; color?: string | null }) => createTag(name, color));
}

export function useUpdateTag() {
  return useTagMutation(({ id, changes }: { id: string; changes: { name?: string; color?: string | null } }) =>
    updateTag(id, changes),
  );
}

export function useMergeTag() {
  return useTagMutation(({ id, intoId }: { id: string; intoId: string }) => mergeTag(id, intoId));
}

export function useDeleteTag() {
  return useTagMutation((id: string) => deleteTag(id));
}

export function useLinkSuggestions(tableName: string, id: string, enabled = true) {
  return useQuery({
    queryKey: ["link-suggestions", tableName, id],
    queryFn: () => fetchLinkSuggestions(tableName, id),
    enabled: enabled && Boolean(tableName && id),
    staleTime: 60_000,
  });
}
