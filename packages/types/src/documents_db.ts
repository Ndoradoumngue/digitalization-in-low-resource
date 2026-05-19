import { z } from "zod";

export const DbDocumentSchema = z.object({
  id:                      z.string(),
  table_name:              z.string(),
  document_type:           z.string().nullable(),
  reference_number:        z.string().nullable(),
  date:                    z.string().nullable(),
  organisation:            z.string().nullable(),
  destination_or_subject:  z.string().nullable(),
  signatory:               z.string().nullable(),
  confidence:              z.string().nullable(),
  review_status:           z.string().nullable(),
  ingested_at:             z.string().nullable(),
  source_image_path:       z.string().nullable(),
});
export type DbDocument = z.infer<typeof DbDocumentSchema>;

export const DbDocumentListSchema = z.object({
  total:     z.number(),
  page:      z.number(),
  page_size: z.number(),
  results:   z.array(DbDocumentSchema),
});
export type DbDocumentList = z.infer<typeof DbDocumentListSchema>;

export const DbTypeSchema = z.object({
  table_name:    z.string(),
  document_type: z.string(),
  count:         z.number(),
  last_ingested: z.string().nullable(),
});
export type DbType = z.infer<typeof DbTypeSchema>;

export const DbTypesSchema = z.array(DbTypeSchema);

// Detail returns all fields — strongly typed only for the common base set;
// the rest are opaque extra fields from the dynamic table.
export const DbDocumentDetailSchema = z.record(z.string(), z.unknown());
export type DbDocumentDetail = z.infer<typeof DbDocumentDetailSchema>;
