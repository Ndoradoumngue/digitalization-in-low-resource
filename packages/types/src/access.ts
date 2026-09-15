import { z } from "zod";

// ── Groups ────────────────────────────────────────────────────────────────────

export const GroupSchema = z.object({
  id:           z.string(),
  name:         z.string(),
  created_at:   z.string(),
  member_count: z.number(),
});
export type Group = z.infer<typeof GroupSchema>;

export const GroupsResponseSchema = z.array(GroupSchema);

export const CreateGroupResponseSchema = z.object({ id: z.string(), name: z.string() });

// ── Users (admin view) ───────────────────────────────────────────────────────

export const AdminUserSchema = z.object({
  id:                 z.string(),
  email:              z.string(),
  full_name:          z.string().nullable(),
  role:               z.enum(["admin", "reviewer"]),
  can_manage_access:  z.boolean(),
  group_ids:          z.array(z.string()),
});
export type AdminUser = z.infer<typeof AdminUserSchema>;

export const AdminUsersResponseSchema = z.array(AdminUserSchema);

// ── Document access grants ───────────────────────────────────────────────────

export const AccessGrantSchema = z.object({
  id:            z.string(),
  grantee_type:  z.enum(["group", "user"]),
  grantee_id:    z.string(),
  grantee_name:  z.string(),
  granted_by:    z.string().nullable(),
  granted_at:    z.string().nullable(),
});
export type AccessGrant = z.infer<typeof AccessGrantSchema>;

export const DocumentAccessResponseSchema = z.object({
  grants:     z.array(AccessGrantSchema),
  can_manage: z.boolean(),
});
export type DocumentAccessResponse = z.infer<typeof DocumentAccessResponseSchema>;

export const CreateAccessGrantResponseSchema = z.object({ id: z.string() });

// ── Fixity / integrity check ─────────────────────────────────────────────────

export const IntegrityCheckResultSchema = z.object({
  checked:    z.number(),
  ok:         z.number(),
  mismatched: z.number(),
  missing:    z.number(),
});
export type IntegrityCheckResult = z.infer<typeof IntegrityCheckResultSchema>;
