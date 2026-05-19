import { z } from "zod";

export const UserSchema = z.object({
  id:        z.string(),
  email:     z.string(),
  full_name: z.string().nullable(),
  role:      z.enum(["admin", "reviewer"]),
});
export type User = z.infer<typeof UserSchema>;

export const LoginResponseSchema = UserSchema;
