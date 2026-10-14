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

/** May create external share links at all (admin, or the can_share flag). */
export function canShare(user: User | null | undefined): boolean {
  return user?.role === "admin" || ((user?.role === "reviewer" || user?.role === "reader") && !!user.can_share);
}

/** Secret documents are never shared, confidential ones only by admins. */
export function canShareLevel(user: User | null | undefined, confidentiality: string | null | undefined): boolean {
  if (confidentiality === "secret") return false;
  if (confidentiality === "confidential") return user?.role === "admin";
  return true;
}

/** Archivist actions (archive, trash, restore, retention, legal holds,
 * destruction proposals) - admin, or a reviewer with can_manage_lifecycle. */
export function canManageLifecycle(user: User | null | undefined): boolean {
  return user?.role === "admin" || (user?.role === "reviewer" && !!user.can_manage_lifecycle);
}

/** Add / update / (de)activate accounts - admin, or a reviewer with
 * can_manage_users. Deleting an account stays admin-only. */
export function canManageUsers(user: User | null | undefined): boolean {
  return user?.role === "admin" || (user?.role === "reviewer" && !!user.can_manage_users);
}
