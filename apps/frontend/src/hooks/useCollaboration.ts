import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  addComment,
  cancelSignatureRequest,
  checkinDocument,
  checkoutDocument,
  createSignatureRequest,
  declineSignature,
  deleteComment,
  editComment,
  fetchComments,
  fetchDocumentLock,
  fetchMentionable,
  fetchMyPendingSignatures,
  fetchNotificationPreferences,
  fetchNotifications,
  fetchSignatureRequests,
  fetchSignatureVerification,
  fetchSigningStatus,
  fetchUnreadNotificationCount,
  forceUnlockDocument,
  markAllNotificationsRead,
  markNotificationRead,
  signDocument,
  updateNotificationPreferences,
} from "@sdai/api-client";
import type { SignatureRequestBody } from "@sdai/types";

// The bell polls - a notification created on the other node arrives with
// the next sync cycle anyway, so once a minute is plenty.
const BELL_POLL_MS = 60_000;

function invalidate(queryClient: QueryClient, keys: string[]) {
  for (const key of keys) queryClient.invalidateQueries({ queryKey: [key] });
}

// ── Notifications ─────────────────────────────────────────────────────────────

export function useUnreadNotificationCount(enabled = true) {
  return useQuery({
    queryKey: ["notifications-unread"],
    queryFn: fetchUnreadNotificationCount,
    refetchInterval: BELL_POLL_MS,
    enabled,
  });
}

export function useNotifications(unreadOnly = false) {
  return useQuery({
    queryKey: ["notifications", unreadOnly],
    queryFn: () => fetchNotifications({ unreadOnly, limit: 100 }),
    refetchInterval: BELL_POLL_MS,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string | "all") => (id === "all" ? markAllNotificationsRead() : markNotificationRead(id)),
    onSuccess: () => invalidate(queryClient, ["notifications", "notifications-unread"]),
  });
}

export function useNotificationPreferences() {
  return useQuery({ queryKey: ["notification-preferences"], queryFn: fetchNotificationPreferences });
}

export function useUpdateNotificationPreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateNotificationPreferences,
    onSuccess: (data) => queryClient.setQueryData(["notification-preferences"], data),
  });
}

// ── Check-out ─────────────────────────────────────────────────────────────────

// A lock changes what everyone may do on the document.
const LOCK_KEYS = [
  "document-lock",
  "document-access",
  "db-document",
  "db-documents",
  "document-versions",
  "document-history",
  "review-queue",
  "review-count",
];

export function useDocumentLock(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-lock", tableName, id],
    queryFn: () => fetchDocumentLock(tableName, id),
    enabled: !!tableName && !!id,
    refetchInterval: BELL_POLL_MS,
  });
}

export type LockAction =
  | { kind: "checkout"; hours?: number }
  | { kind: "checkin"; file?: File | null; comment?: string; reextract?: boolean }
  | { kind: "unlock" };

export function useLockAction(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: LockAction): Promise<unknown> => {
      if (action.kind === "checkout") return checkoutDocument(tableName, id, action.hours);
      if (action.kind === "checkin") return checkinDocument(tableName, id, action);
      return forceUnlockDocument(tableName, id);
    },
    onSuccess: () => invalidate(queryClient, LOCK_KEYS),
  });
}

// ── Comments ──────────────────────────────────────────────────────────────────

export function useComments(tableName: string, id: string) {
  return useQuery({
    queryKey: ["document-comments", tableName, id],
    queryFn: () => fetchComments(tableName, id),
    enabled: !!tableName && !!id,
  });
}

export function useMentionable(tableName: string, id: string, q: string, enabled: boolean) {
  return useQuery({
    queryKey: ["mentionable", tableName, id, q],
    queryFn: () => fetchMentionable(tableName, id, q),
    enabled: enabled && !!tableName && !!id,
    staleTime: 30_000,
  });
}

export function useCommentAction(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      action:
        | { kind: "add"; body: string; parentId?: string | null; mentions: string[] }
        | { kind: "edit"; commentId: string; body: string; mentions: string[] }
        | { kind: "delete"; commentId: string },
    ): Promise<unknown> => {
      if (action.kind === "add") {
        return addComment(tableName, id, { body: action.body, parent_id: action.parentId ?? null, mentions: action.mentions });
      }
      if (action.kind === "edit") return editComment(action.commentId, { body: action.body, mentions: action.mentions });
      return deleteComment(action.commentId);
    },
    onSuccess: () => invalidate(queryClient, ["document-comments", "document-history"]),
  });
}

// ── Signatures ────────────────────────────────────────────────────────────────

const SIGNATURE_KEYS = [
  "signature-requests",
  "signature-verification",
  "my-pending-signatures",
  "document-versions",
  "document-history",
  "db-document",
  "notifications",
  "notifications-unread",
];

export function useSigningStatus() {
  return useQuery({ queryKey: ["signing-status"], queryFn: fetchSigningStatus, staleTime: 5 * 60_000 });
}

export function useMyPendingSignatures(enabled = true) {
  return useQuery({
    queryKey: ["my-pending-signatures"],
    queryFn: fetchMyPendingSignatures,
    refetchInterval: BELL_POLL_MS,
    enabled,
  });
}

export function useSignatureRequests(tableName: string, id: string) {
  return useQuery({
    queryKey: ["signature-requests", tableName, id],
    queryFn: () => fetchSignatureRequests(tableName, id),
    enabled: !!tableName && !!id,
  });
}

export function useSignatureVerification(tableName: string, id: string, version?: number) {
  return useQuery({
    queryKey: ["signature-verification", tableName, id, version ?? null],
    queryFn: () => fetchSignatureVerification(tableName, id, version),
    enabled: !!tableName && !!id,
  });
}

export function useSignatureAction(tableName: string, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (
      action:
        | { kind: "request"; body: SignatureRequestBody }
        | { kind: "sign"; requestId: string; password: string; reason?: string }
        | { kind: "decline"; requestId: string; note?: string }
        | { kind: "cancel"; requestId: string; note?: string },
    ): Promise<unknown> => {
      if (action.kind === "request") return createSignatureRequest(tableName, id, action.body);
      if (action.kind === "sign") return signDocument(action.requestId, action.password, action.reason);
      if (action.kind === "decline") return declineSignature(action.requestId, action.note);
      return cancelSignatureRequest(action.requestId, action.note);
    },
    onSuccess: () => invalidate(queryClient, SIGNATURE_KEYS),
  });
}
