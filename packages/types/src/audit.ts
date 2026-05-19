import { z } from "zod";

export const AuditLogEntrySchema = z.object({
  id:          z.string(),
  user_id:     z.string().nullable(),
  user_email:  z.string().nullable(),
  action:      z.string(),
  table_name:  z.string().nullable(),
  document_id: z.string().nullable(),
  details:     z.record(z.unknown()).nullable(),
  ip_address:  z.string().nullable(),
  created_at:  z.string(),
});
export type AuditLogEntry = z.infer<typeof AuditLogEntrySchema>;

export const AuditLogPageSchema = z.object({
  total:     z.number(),
  page:      z.number(),
  page_size: z.number(),
  items:     z.array(AuditLogEntrySchema),
});
export type AuditLogPage = z.infer<typeof AuditLogPageSchema>;
