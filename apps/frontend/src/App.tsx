import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ConfirmProvider } from "./context/ConfirmContext";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import NotificationsPage from "./pages/NotificationsPage";
import AdminRoute from "./components/auth/AdminRoute";
import ContributorRoute from "./components/auth/ContributorRoute";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import UploadPage from "./pages/UploadPage";
import DocumentsPage from "./pages/DocumentsPage";
import ExportsPage from "./pages/ExportsPage";
import DataBrowserPage from "./pages/DataBrowserPage";
import DataBrowserDetailPage from "./pages/DataBrowserDetailPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import SchemaPage from "./pages/SchemaPage";
import ReviewPage from "./pages/ReviewPage";
import AuditPage from "./pages/AuditPage";
import BenchmarksPage from "./pages/BenchmarksPage";
import AccessAdminPage from "./pages/AccessAdminPage";
import SyncAdminPage from "./pages/SyncAdminPage";
import LifecycleRoute from "./components/auth/LifecycleRoute";
import TrashPage from "./pages/TrashPage";
import RetentionRulesPage from "./pages/RetentionRulesPage";
import DestructionsPage from "./pages/DestructionsPage";
import UsersPage from "./pages/UsersPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import UserManagerRoute from "./components/auth/UserManagerRoute";

/** A temporary password must be replaced before anything else (the backend
 * refuses every other call meanwhile). */
function PasswordChangeGate() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  if (user?.must_change_password && pathname !== "/account/password") {
    return <Navigate to="/account/password" replace />;
  }
  return null;
}

export default function App() {
  return (
    <AuthProvider>
      <ConfirmProvider>
      <PasswordChangeGate />
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route
          path="/"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/upload"
          element={
            <ContributorRoute>
              <UploadPage />
            </ContributorRoute>
          }
        />

        <Route
          path="/documents"
          element={
            <ProtectedRoute>
              <DocumentsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/exports"
          element={
            <ProtectedRoute>
              <ExportsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/documents/:tableName/:id"
          element={
            <ProtectedRoute>
              <DocumentDetailPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/ops/data"
          element={
            <ProtectedRoute>
              <DataBrowserPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/ops/data/:tableName/:id"
          element={
            <ProtectedRoute>
              <DataBrowserDetailPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/ops/access"
          element={
            <AdminRoute>
              <AccessAdminPage />
            </AdminRoute>
          }
        />

        <Route
          path="/ops/sync"
          element={
            <AdminRoute>
              <SyncAdminPage />
            </AdminRoute>
          }
        />

        <Route
          path="/lifecycle/trash"
          element={
            <LifecycleRoute>
              <TrashPage />
            </LifecycleRoute>
          }
        />

        <Route
          path="/lifecycle/retention"
          element={
            <LifecycleRoute>
              <RetentionRulesPage />
            </LifecycleRoute>
          }
        />

        <Route
          path="/lifecycle/destructions"
          element={
            <LifecycleRoute>
              <DestructionsPage />
            </LifecycleRoute>
          }
        />

        <Route
          path="/schema"
          element={
            <ProtectedRoute>
              <SchemaPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/review"
          element={
            <ContributorRoute>
              <ReviewPage />
            </ContributorRoute>
          }
        />

        <Route
          path="/admin/audit"
          element={
            <AdminRoute>
              <AuditPage />
            </AdminRoute>
          }
        />

        <Route
          path="/admin/benchmarks"
          element={
            <AdminRoute>
              <BenchmarksPage />
            </AdminRoute>
          }
        />

        <Route
          path="/notifications"
          element={
            <ProtectedRoute>
              <NotificationsPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/ops/users"
          element={
            <UserManagerRoute>
              <UsersPage />
            </UserManagerRoute>
          }
        />

        <Route
          path="/account/password"
          element={
            <ProtectedRoute>
              <ChangePasswordPage />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </ConfirmProvider>
    </AuthProvider>
  );
}
