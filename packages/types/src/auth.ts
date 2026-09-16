import { z } from "zod";

export const UserSchema = z.object({
  id:          z.string(),
  email:       z.string(),
  full_name:   z.string().nullable(),
  role:        z.enum(["admin", "reviewer"]),
  tenant_slug: z.string(),
  tenant_name: z.string(),
  can_manage_access: z.boolean(),
  can_edit_extraction: z.boolean(),
});
export type User = z.infer<typeof UserSchema>;

export const LoginResponseSchema = UserSchema;
