import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { canManageLifecycle } from "../../utils/permissions";

/** Archivist pages (trash, retention rules, destructions) - admins and
 * reviewers with can_manage_lifecycle; the backend refuses anyone else. */
export default function LifecycleRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="h-8 w-8 rounded-full border-4 border-indigo-200 border-t-indigo-600 animate-spin" />
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;
  if (!canManageLifecycle(user)) return <Navigate to="/documents" replace />;

  return <>{children}</>;
}
