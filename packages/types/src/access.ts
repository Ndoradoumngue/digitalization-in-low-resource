import { z } from "zod";
import { AuditChainSummarySchema } from "./versions";
import { ConfidentialitySchema } from "./auth";

// Cumulative grant levels, lowest first: read (view/search) < download
// (export, original file) < edit (change content, review, file, link).
export const PERMISSION_LEVELS = ["read", "download", "edit"] as const;
export const PermissionLevelSchema = z.enum(PERMISSION_LEVELS);
export type PermissionLevel = z.infer<typeof PermissionLevelSchema>;

export const GrantScopeSchema = z.enum(["document", "series", "doc_type"]);
export type GrantScope = z.infer<typeof GrantScopeSchema>;

// ── Groups ────────────────────────────────────────────────────────────────────

export const GroupSchema = z.object({
  id: z.string(),
  name: z.string(),
  created_at: z.string(),
  member_count: z.number(),
});
export type Group = z.infer<typeof GroupSchema>;

export const GroupsResponseSchema = z.array(GroupSchema);

export const CreateGroupResponseSchema = z.object({ id: z.string(), name: z.string() });

// ── Users (admin view) ───────────────────────────────────────────────────────

export const AdminUserSchema = z.object({
  id: z.string(),
  email: z.string(),
  full_name: z.string().nullable(),
  role: z.enum(["admin", "reviewer", "reader"]),
  can_manage_access: z.boolean(),
  can_edit_extraction: z.boolean(),
  can_manage_lifecycle: z.boolean(),
  can_share: z.boolean(),
  clearance: ConfidentialitySchema,
  group_ids: z.array(z.string()),
  // User management (GED Phase 9)
  can_manage_users: z.boolean().default(false),
  is_active: z.boolean().default(true),
  must_change_password: z.boolean().default(false),
  created_at: z.string().nullable().optional(),
  last_login_at: z.string().nullable().optional(),
});
export type AdminUser = z.infer<typeof AdminUserSchema>;

/** Delegated permissions an account can hold (users_router.FLAGS). */
export const USER_FLAGS = [
  "can_manage_access",
  "can_edit_extraction",
  "can_manage_lifecycle",
  "can_share",
  "can_manage_users",
] as const;
export type UserFlag = (typeof USER_FLAGS)[number];

export const AdminUsersResponseSchema = z.array(AdminUserSchema);

// ── Document access grants ───────────────────────────────────────────────────

export const AccessGrantSchema = z.object({
  id: z.string(),
  scope: GrantScopeSchema,
  table_name: z.string().nullable(),
  series_id: z.string().nullable(),
  series_name: z.string().nullable(),
  permission: PermissionLevelSchema,
  grantee_type: z.enum(["group", "user"]),
  grantee_id: z.string(),
  grantee_name: z.string(),
  granted_by: z.string().nullable(),
  granted_at: z.string().nullable(),
});
export type AccessGrant = z.infer<typeof AccessGrantSchema>;

export const DocumentAccessResponseSchema = z.object({
  // the document's own grants plus those inherited from its series / type
  grants: z.array(AccessGrantSchema),
  can_manage: z.boolean(),
  // the caller's own highest level on this document
  my_permission: PermissionLevelSchema,
});
export type DocumentAccessResponse = z.infer<typeof DocumentAccessResponseSchema>;

export const CreateAccessGrantResponseSchema = z.object({ id: z.string() });

export const ScopedGrantsResponseSchema = z.object({ grants: z.array(AccessGrantSchema) });
export type ScopedGrantsResponse = z.infer<typeof ScopedGrantsResponseSchema>;

// ── Grantees (minimal groups/users listing for the tagging picker) ──────────
// Deliberately a smaller shape than GroupSchema/AdminUserSchema - no
// role, can_manage_access, or member_count, since GET /api/db/grantees is
// available to any access manager, not just admins (see admin_router.py's
// GET /api/admin/groups and /api/admin/users, which stay admin-only).

export const GranteeGroupSchema = z.object({ id: z.string(), name: z.string() });
export type GranteeGroup = z.infer<typeof GranteeGroupSchema>;

export const GranteeUserSchema = z.object({
  id: z.string(),
  email: z.string(),
  full_name: z.string().nullable(),
});
export type GranteeUser = z.infer<typeof GranteeUserSchema>;

export const GranteesResponseSchema = z.object({
  groups: z.array(GranteeGroupSchema),
  users: z.array(GranteeUserSchema),
});
export type GranteesResponse = z.infer<typeof GranteesResponseSchema>;

// ── Fixity / integrity check ─────────────────────────────────────────────────

// Fixity counts - documents (checked/ok) and, since GED Phase 2, every
// version file (versions_checked/versions_ok); mismatched/missing cover both.
export const FixityCountsSchema = z.object({
  checked: z.number(),
  ok: z.number(),
  mismatched: z.number(),
  missing: z.number(),
  versions_checked: z.number().optional(),
  versions_ok: z.number().optional(),
});
export type FixityCounts = z.infer<typeof FixityCountsSchema>;

export const IntegrityCheckResultSchema = FixityCountsSchema.extend({
  audit_chain: AuditChainSummarySchema.optional(),
});
export type IntegrityCheckResult = z.infer<typeof IntegrityCheckResultSchema>;

export const LastIntegrityRunSchema = z.object({
  last_run: z
    .object({
      ran_at: z.string(),
      ran_by: z.string().nullable(),
      fixity: FixityCountsSchema.nullable(),
      audit_chain: AuditChainSummarySchema.nullable(),
    })
    .nullable(),
});
export type LastIntegrityRun = z.infer<typeof LastIntegrityRunSchema>;
