import { z } from "zod";

export const SeriesSchema = z.object({
  id:          z.string(),
  name:        z.string(),
  description: z.string().nullable(),
  created_at:  z.string().nullable(),
});
export type Series = z.infer<typeof SeriesSchema>;

export const SeriesListResponseSchema = z.array(SeriesSchema);

export const CreateSeriesResponseSchema = z.object({ id: z.string(), name: z.string() });
