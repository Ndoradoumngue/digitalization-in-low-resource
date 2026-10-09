import { PERMISSION_LEVELS, type PermissionLevel, type User } from "@sdai/types";

// UI-side mirror of the backend rules (apps/api/auth.py and
// documents_router._access_where_clause) - used only to hide actions the
// backend would refuse anyway. The backend stays the authority.

export function isReader(user: User | null | undefined): boolean {
  return user?.role === "reader";
}

/** Admin or reviewer - may review, upload, file, link. */
export function isActor(user: User | null | undefined): boolean {
  return user?.role === "admin" || user?.role === "reviewer";
}

export function canEditExtraction(user: User | null | undefined): boolean {
  return user?.role === "admin" || (user?.role === "reviewer" && user.can_edit_extraction);
}

export function canManageAccess(user: User | null | undefined): boolean {
  return user?.role === "admin" || (user?.role === "reviewer" && user.can_manage_access);
}

export function atLeast(level: PermissionLevel | null | undefined, needed: PermissionLevel): boolean {
  if (!level) return false;
  return PERMISSION_LEVELS.indexOf(level) >= PERMISSION_LEVELS.indexOf(needed);
}
