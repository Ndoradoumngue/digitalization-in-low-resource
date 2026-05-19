import { z } from "zod";

export const ReviewQueueItemSchema = z.object({
  id:                     z.string(),
  table_name:             z.string(),
  document_type:          z.string().nullable(),
  reference_number:       z.string().nullable(),
  date:                   z.string().nullable(),
  organisation:           z.string().nullable(),
  destination_or_subject: z.string().nullable(),
  signatory:              z.string().nullable(),
  confidence:             z.string().nullable(),
  review_status:          z.string().nullable(),
  ingested_at:            z.string().nullable(),
  source_image_path:      z.string().nullable(),
});

export const ReviewQueueSchema = z.object({
  total: z.number(),
  items: z.array(ReviewQueueItemSchema),
});

export const ReviewCountSchema = z.object({
  pending: z.number(),
});

export type ReviewQueueItem = z.infer<typeof ReviewQueueItemSchema>;
export type ReviewQueue     = z.infer<typeof ReviewQueueSchema>;
export type ReviewCount     = z.infer<typeof ReviewCountSchema>;
