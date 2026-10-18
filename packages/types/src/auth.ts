import { z } from "zod";

// Ordered lowest to highest - a user's clearance is the highest document
// confidentiality level they may see (admins see every level).
export const CONFIDENTIALITY_LEVELS = ["public", "internal", "confidential", "secret"] as const;
export const ConfidentialitySchema = z.enum(CONFIDENTIALITY_LEVELS);
export type Confidentiality = z.infer<typeof ConfidentialitySchema>;

export const UserSchema = z.object({
  id:          z.string(),
  email:       z.string(),
  full_name:   z.string().nullable(),
  role:        z.enum(["admin", "reviewer", "reader"]),
  tenant_slug: z.string(),
  tenant_name: z.string(),
  can_manage_access: z.boolean(),
  can_edit_extraction: z.boolean(),
  can_manage_lifecycle: z.boolean().default(false),
  can_share: z.boolean().default(false),
  clearance: ConfidentialitySchema.default("internal"),
  // User management (GED Phase 9)
  can_manage_users: z.boolean().default(false),
  must_change_password: z.boolean().default(false),
  // My profile (profile_router) - avatar_url is versioned, null without a picture.
  job_title:   z.string().nullable().optional(),
  phone:       z.string().nullable().optional(),
  avatar_url:  z.string().nullable().optional(),
  // Which server answered (hybrid local + cloud deployment, see README).
  node_id:     z.string().optional(),
  node_role:   z.enum(["local", "cloud"]).optional(),
});
export type User = z.infer<typeof UserSchema>;

export const LoginResponseSchema = UserSchema;

export const ProfileSchema = z.object({
  full_name: z.string().nullable(),
  job_title: z.string().nullable(),
  phone:     z.string().nullable(),
});
export type Profile = z.infer<typeof ProfileSchema>;

export const AvatarResponseSchema = z.object({
  avatar_url: z.string().nullable(),
});
