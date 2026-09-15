import { z } from "zod";

export const ReviewQueueItemSchema = z.object({
  id:                     z.string(),
  table_name:             z.string(),
  document_type:          z.string().nullable(),
  record_id:              z.string().nullable(),
  confidence:             z.string().nullable(),
  review_status:          z.string().nullable(),
  ingested_at:            z.string().nullable(),
  source_image_path:      z.string().nullable(),
  extra_fields:           z.record(z.string(), z.unknown()),
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
