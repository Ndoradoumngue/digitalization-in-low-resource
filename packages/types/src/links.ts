import { z } from "zod";

export const DocumentLinkSchema = z.object({
  id:                       z.string(),
  relation:                 z.string(),
  note:                     z.string().nullable(),
  created_at:               z.string().nullable(),
  created_by:               z.string().nullable(),
  direction:                z.enum(["outgoing", "incoming"]),
  other_table:              z.string(),
  other_id:                 z.string(),
  other_document_type:      z.string().nullable(),
  other_display:            z.string().nullable(),
  other_source_image_path:  z.string().nullable(),
  other_missing:            z.boolean(),
  // GED Phase 6 - the linked document was destroyed: only its tombstone remains.
  other_destroyed:          z.boolean().optional(),
  other_record_id:          z.string().nullable().optional(),
});
export type DocumentLink = z.infer<typeof DocumentLinkSchema>;

export const DocumentLinksResponseSchema = z.object({
  links: z.array(DocumentLinkSchema),
});
export type DocumentLinksResponse = z.infer<typeof DocumentLinksResponseSchema>;

export const CreateLinkResponseSchema = z.object({ id: z.string() });
