import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import AdminRoute from "./components/auth/AdminRoute";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import UploadPage from "./pages/UploadPage";
import DocumentsPage from "./pages/DocumentsPage";
import DataBrowserPage from "./pages/DataBrowserPage";
import DataBrowserDetailPage from "./pages/DataBrowserDetailPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import SchemaPage from "./pages/SchemaPage";
import ReviewPage from "./pages/ReviewPage";
import AuditPage from "./pages/AuditPage";
import BenchmarksPage from "./pages/BenchmarksPage";
import AccessAdminPage from "./pages/AccessAdminPage";

export default function App() {
  return (
    <AuthProvider>
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
            <ProtectedRoute>
              <UploadPage />
            </ProtectedRoute>
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
            <ProtectedRoute>
              <ReviewPage />
            </ProtectedRoute>
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

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
