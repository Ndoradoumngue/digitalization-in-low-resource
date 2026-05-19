import { z } from "zod";

export const DbColumnSchema = z.object({
  name:     z.string(),
  type:     z.string(),
  nullable: z.boolean(),
});

export const DbForeignKeySchema = z.object({
  column:            z.string(),
  references_table:  z.string(),
  references_column: z.string(),
});

export const DbTableInfoSchema = z.object({
  name:          z.string(),
  document_type: z.string(),
  row_count:     z.number(),
  last_ingested: z.string().nullable(),
  columns:       z.array(DbColumnSchema),
  foreign_keys:  z.array(DbForeignKeySchema),
});

export const DbSchemaSchema = z.object({
  tables: z.array(DbTableInfoSchema),
});

export type DbColumn      = z.infer<typeof DbColumnSchema>;
export type DbForeignKey  = z.infer<typeof DbForeignKeySchema>;
export type DbTableInfo   = z.infer<typeof DbTableInfoSchema>;
export type DbSchema      = z.infer<typeof DbSchemaSchema>;
