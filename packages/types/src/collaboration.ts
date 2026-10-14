import { z } from "zod";

// ── Notifications, check-out, comments, electronic signature (GED Phase 7) ───

export const NOTIFICATION_KINDS = [
  "review_pending",
  "document_shared",
  "mention",
  "comment_reply",
  "retention_eligible",
  "destruction_proposed",
  "sync_down",
  "backup_failed",
  "signature_requested",
  "signature_completed",
  "signature_declined",
  "lock_released",
] as const;
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number];

export const NotificationSchema = z.object({
  id:          z.string(),
  // Unknown kinds (a newer peer node) still list, with a generic text.
  kind:        z.string(),
  table_name:  z.string().nullable(),
  document_id: z.string().nullable(),
  payload:     z.record(z.string(), z.unknown()),
  link:        z.string(),
  created_at:  z.string().nullable(),
  read_at:     z.string().nullable(),
});
export type Notification = z.infer<typeof NotificationSchema>;
export const NotificationListSchema = z.array(NotificationSchema);

export const UnreadCountSchema = z.object({ count: z.number() });

export const EMAIL_PREFERENCES = ["immediate", "digest", "off"] as const;
export type EmailPreference = (typeof EMAIL_PREFERENCES)[number];

export const NotificationPreferencesSchema = z.object({
  email_notifications: z.enum(EMAIL_PREFERENCES),
  locale:              z.string().nullable(),
  email_enabled:       z.boolean(),
});
export type NotificationPreferences = z.infer<typeof NotificationPreferencesSchema>;

// ── Check-out ────────────────────────────────────────────────────────────────

export const DocumentLockSchema = z.object({
  locked:           z.boolean(),
  locked_by:        z.string().nullable(),
  locked_by_name:   z.string().nullable(),
  locked_until:     z.string().nullable(),
  mine:             z.boolean(),
  can_checkout:     z.boolean(),
  can_checkin:      z.boolean(),
  can_force_unlock: z.boolean(),
  default_hours:    z.number(),
  max_hours:        z.number(),
});
export type DocumentLock = z.infer<typeof DocumentLockSchema>;

// ── Comments ─────────────────────────────────────────────────────────────────

export const CommentSchema = z.object({
  id:          z.string(),
  parent_id:   z.string().nullable(),
  version_no:  z.number().nullable(),
  author_id:   z.string(),
  author_name: z.string().nullable(),
  body:        z.string().nullable(),
  mentions:    z.array(z.object({ id: z.string(), name: z.string() })),
  created_at:  z.string().nullable(),
  edited_at:   z.string().nullable(),
  deleted:     z.boolean(),
  can_edit:    z.boolean(),
  can_delete:  z.boolean(),
});
export type Comment = z.infer<typeof CommentSchema>;
export const CommentListSchema = z.array(CommentSchema);

export const MentionableUserSchema = z.object({
  id:    z.string(),
  name:  z.string(),
  email: z.string(),
});
export type MentionableUser = z.infer<typeof MentionableUserSchema>;
export const MentionableUserListSchema = z.array(MentionableUserSchema);

// ── Electronic signature ─────────────────────────────────────────────────────

export const SIGNATURE_REQUEST_STATUSES = ["pending", "completed", "declined", "cancelled"] as const;
export type SignatureRequestStatus = (typeof SIGNATURE_REQUEST_STATUSES)[number];

export const SignatureRequestSchema = z.object({
  id:                z.string(),
  table_name:        z.string(),
  document_id:       z.string(),
  requested_by:      z.string().nullable(),
  requested_by_name: z.string().nullable(),
  reason:            z.string(),
  message:           z.string().nullable(),
  status:            z.enum(SIGNATURE_REQUEST_STATUSES),
  created_at:        z.string().nullable(),
  closed_at:         z.string().nullable(),
  close_note:        z.string().nullable(),
  signers: z.array(
    z.object({
      user_id:      z.string(),
      name:         z.string(),
      order:        z.number().optional(),
      title:        z.string().nullable().optional(),
      status:       z.enum(["pending", "signed", "declined"]),
      signed_at:    z.string().nullable().optional(),
      signature_id: z.string().nullable().optional(),
      is_next:      z.boolean(),
    }),
  ),
  can_sign:   z.boolean(),
  can_cancel: z.boolean(),
  record_id:  z.string().nullable().optional(),
});
export type SignatureRequest = z.infer<typeof SignatureRequestSchema>;
export const SignatureRequestListSchema = z.array(SignatureRequestSchema);

export const SigningStatusSchema = z.object({
  configured:  z.boolean(),
  reason:      z.string().nullable().optional(),
  subject:     z.string().optional(),
  issuer:      z.string().optional(),
  fingerprint: z.string().optional(),
  not_after:   z.string().optional(),
  expired:     z.boolean().optional(),
  self_signed: z.boolean().optional(),
});
export type SigningStatus = z.infer<typeof SigningStatusSchema>;

export const SignatureEvidenceSchema = z.object({
  id:                z.string(),
  request_id:        z.string().nullable(),
  version_no:        z.number(),
  source_version_no: z.number(),
  field_name:        z.string(),
  signer_id:         z.string(),
  signer_name:       z.string(),
  signer_title:      z.string().nullable(),
  reason:            z.string(),
  level:             z.string(),
  signed_at:         z.string(),
  ip_address:        z.string().nullable(),
  cert_subject:      z.string(),
  cert_fingerprint:  z.string(),
  source_file_hash:  z.string().nullable(),
  signed_file_hash:  z.string(),
});
export type SignatureEvidence = z.infer<typeof SignatureEvidenceSchema>;

export const EmbeddedSignatureSchema = z.object({
  field:              z.string(),
  signer_name:        z.string().nullable(),
  reason:             z.string().nullable(),
  intact:             z.boolean(),
  valid:              z.boolean(),
  trusted:            z.boolean(),
  ok:                 z.boolean(),
  coverage:           z.string().nullable().optional(),
  modification_level: z.string().nullable().optional(),
  signed_at:          z.string().nullable().optional(),
  cert_subject:       z.string().optional(),
  cert_fingerprint:   z.string().optional(),
  summary:            z.string().optional(),
  error:              z.string().optional(),
  evidence_id:        z.string().nullable().optional(),
  evidence_matches:   z.boolean().optional(),
});
export type EmbeddedSignature = z.infer<typeof EmbeddedSignatureSchema>;

export const SignatureVerificationSchema = z.object({
  level:    z.string(),
  evidence: z.array(SignatureEvidenceSchema),
  verification: z
    .object({
      version_no:   z.number(),
      file_hash:    z.string().optional(),
      file_hash_ok: z.boolean().optional(),
      signatures:   z.array(EmbeddedSignatureSchema),
      all_ok:       z.boolean(),
      not_pdf:      z.boolean().optional(),
    })
    .nullable(),
  audit: z.array(
    z.object({
      action:     z.string(),
      user_email: z.string().nullable(),
      created_at: z.string().nullable(),
      details:    z.unknown(),
    }),
  ),
});
export type SignatureVerification = z.infer<typeof SignatureVerificationSchema>;

export interface SignatureRequestBody {
  signers: { user_id: string; title?: string }[];
  reason: string;
  message?: string;
}

// ── Admin: this server's e-mail outbox ───────────────────────────────────────

export const OutboxEmailSchema = z.object({
  id:              z.string(),
  kind:            z.string(),
  to_email:        z.string(),
  subject:         z.string(),
  status:          z.enum(["pending", "sending", "sent", "failed"]),
  attempts:        z.number(),
  last_error:      z.string().nullable(),
  created_at:      z.string().nullable(),
  next_attempt_at: z.string().nullable(),
  sent_at:         z.string().nullable(),
});
export type OutboxEmail = z.infer<typeof OutboxEmailSchema>;

export const OutboxResponseSchema = z.object({
  email_enabled: z.boolean(),
  counts: z.object({ pending: z.number(), sending: z.number(), sent: z.number(), failed: z.number() }),
  items: z.array(OutboxEmailSchema),
});
export type OutboxResponse = z.infer<typeof OutboxResponseSchema>;
