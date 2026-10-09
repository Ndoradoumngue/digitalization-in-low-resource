import { describe, expect, it } from "vitest";
import { AdminUserSchema, DocumentAccessResponseSchema, UserSchema } from "../index";

const grant = {
  id: "g1",
  scope: "series",
  table_name: null,
  series_id: "s1",
  series_name: "Land deeds",
  permission: "download",
  grantee_type: "group",
  grantee_id: "grp",
  grantee_name: "HR",
  granted_by: null,
  granted_at: null,
};

describe("access schemas (GED Phase 1)", () => {
  it("parses a document access response with inherited grants and own level", () => {
    const parsed = DocumentAccessResponseSchema.parse({ grants: [grant], can_manage: false, my_permission: "read" });
    expect(parsed.grants[0].scope).toBe("series");
    expect(parsed.my_permission).toBe("read");
  });

  it("rejects an unknown permission level", () => {
    expect(() =>
      DocumentAccessResponseSchema.parse({ grants: [{ ...grant, permission: "delete" }], can_manage: false, my_permission: "read" }),
    ).toThrow();
  });

  it("accepts the reader role and defaults the new user fields", () => {
    const user = UserSchema.parse({
      id: "u", email: "r@x", full_name: null, role: "reader", tenant_slug: "t", tenant_name: "T",
      can_manage_access: false, can_edit_extraction: false,
    });
    expect(user.clearance).toBe("internal");
    expect(user.can_share).toBe(false);
  });

  it("requires clearance and every flag on the admin user listing", () => {
    expect(() =>
      AdminUserSchema.parse({
        id: "u", email: "r@x", full_name: null, role: "reader",
        can_manage_access: false, can_edit_extraction: false, group_ids: [],
      }),
    ).toThrow();
  });
});
