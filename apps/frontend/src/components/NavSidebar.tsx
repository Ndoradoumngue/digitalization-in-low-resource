import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { useDbTypes } from "../hooks/useDocumentsDb";
import { useReviewCount } from "../hooks/useReview";
import LanguageSwitcher from "./LanguageSwitcher";
import GlobalSearch from "./GlobalSearch";
import { canManageLifecycle, isReader } from "../utils/permissions";
import { useEligibleDocuments, useDestructions } from "../hooks/useLifecycle";
import { useSyncStatus } from "../hooks/useSync";
import { alertText } from "../pages/SyncAdminPage";

// Alerts worth interrupting an admin for on every page; the rest (no backup
// target configured on a standalone server, ...) stay on the Sync page.
const URGENT_SYNC_ALERTS = new Set(["sync_down", "backup_failed", "backup_stale", "files_failed"]);

function navClass(active: boolean) {
  return `flex items-center gap-2 w-full rounded-lg px-3 py-2 text-sm font-semibold transition-colors ${active ? "bg-indigo-50 text-indigo-700" : "text-gray-700 hover:bg-gray-100"
    }`;
}

export default function NavSidebar() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const { t } = useTranslation();
  const { data: dbTypes } = useDbTypes();
  const dbTotal = (dbTypes ?? []).reduce((acc, t) => acc + t.count, 0);
  const { data: reviewCount } = useReviewCount();
  const pendingReview = reviewCount?.pending ?? 0;
  const { data: syncStatus } = useSyncStatus(user?.role === "admin");
  const syncAlerts = syncStatus?.alerts ?? [];
  const urgentSync = syncAlerts.filter((a) => URGENT_SYNC_ALERTS.has(a.kind));
  // Archivist badge: documents whose retention ended and are not proposed
  // yet, plus (for admins) proposals waiting for someone else's approval.
  const archivist = canManageLifecycle(user);
  const { data: eligible } = useEligibleDocuments(archivist);
  const { data: destructions } = useDestructions(archivist);
  const toPropose = (eligible ?? []).filter((d) => !d.pending_destruction_id && !d.legal_hold).length;
  const toApprove = user?.role === "admin"
    ? (destructions ?? []).filter((r) => r.status === "proposed" && r.proposed_by_id !== user.id).length
    : 0;

  return (
    <aside className="w-72 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden h-screen">
      {/* Header */}
      <div className="px-4 py-4 border-b border-gray-200 flex items-center gap-2.5">
        <img src="/logo.png" alt="SDAI Digitalization" className="w-8 h-8 object-contain flex-shrink-0" />
        <div>
          <h1 className="text-lg font-bold text-indigo-700 tracking-tight leading-tight">
            {t("nav.appName")}
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {t("nav.documentCount", { count: dbTotal })}
          </p>
        </div>
      </div>

      <GlobalSearch />

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 flex flex-col">
        <div className="space-y-1">
          <Link
            to="/documents"
            className={navClass(pathname.startsWith("/documents"))}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z" />
            </svg>
            {t("nav.documents")}
            {dbTotal > 0 && (
              <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-700">
                {dbTotal.toLocaleString()}
              </span>
            )}
          </Link>
          <Link
            to="/exports"
            className={navClass(pathname === "/exports")}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            {t("nav.exports")}
          </Link>
        </div>

        {/* Spacer pushes the rest to the bottom of the sidebar */}
        <div className="flex-1" />

        <div className="space-y-1 pt-2 border-t border-gray-100">
          {!isReader(user) && (
          <Link
            to="/review"
            className={navClass(pathname === "/review")}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
            {t("nav.reviewQueue")}
            {pendingReview > 0 && (
              <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-bold bg-red-500 text-white">
                {pendingReview}
              </span>
            )}
          </Link>
          )}

          {archivist && (
            <>
              <p className="px-3 pt-3 pb-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wide">
                {t("nav.lifecycle")}
              </p>
              <Link to="/lifecycle/destructions" className={navClass(pathname === "/lifecycle/destructions")}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5m6 4.125 2.25 2.25m0 0 2.25 2.25M12 13.875l2.25-2.25M12 13.875l-2.25 2.25M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z" />
                </svg>
                {t("nav.destructions")}
                {toPropose + toApprove > 0 && (
                  <span className="ml-auto inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-bold bg-orange-500 text-white">
                    {toPropose + toApprove}
                  </span>
                )}
              </Link>
              <Link to="/lifecycle/retention" className={navClass(pathname === "/lifecycle/retention")}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                </svg>
                {t("nav.retention")}
              </Link>
              <Link to="/lifecycle/trash" className={navClass(pathname === "/lifecycle/trash")}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                </svg>
                {t("nav.trash")}
              </Link>
            </>
          )}

          <Link
            to="/schema"
            className={navClass(pathname === "/schema")}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
            </svg>
            {t("nav.schema")}
          </Link>
          {user?.role === "admin" && (
            <>
              <Link
                to="/admin/audit"
                className={navClass(pathname === "/admin/audit")}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 0 0 2.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 0 0-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75 2.25 2.25 0 0 0-.1-.664m-5.8 0A2.251 2.251 0 0 1 13.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25Z" />
                </svg>
                {t("nav.auditLog")}
              </Link>
              <Link
                to="/admin/benchmarks"
                className={navClass(pathname === "/admin/benchmarks")}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23-.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.611L5 14.5" />
                </svg>
                {t("nav.ocrVlmBenchmarks")}
              </Link>
            </>
          )}

          <p className="px-3 pt-3 pb-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wide">
            {t("nav.operations")}
          </p>
          <Link
            to="/ops/data"
            className={navClass(pathname === "/ops/data")}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z" />
            </svg>
            {t("nav.dataBrowser")}
          </Link>

          {user?.role === "admin" && (
            <Link
              to="/ops/access"
              className={navClass(pathname === "/ops/access")}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 0 0 3.741-.479 3 3 0 0 0-4.682-2.72m.94 3.198.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0 1 12 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 0 1 6 18.719m12 0a5.971 5.971 0 0 0-.941-3.197m0 0A5.995 5.995 0 0 0 12 12.75a5.995 5.995 0 0 0-5.058 2.772m0 0a3 3 0 0 0-4.681 2.72 8.986 8.986 0 0 0 3.74.477m.94-3.197a5.971 5.971 0 0 0-.94 3.197M15 6.75a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm6 3a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Zm-13.5 0a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z" />
              </svg>
              {t("nav.accessGroups")}
            </Link>
          )}

          {user?.role === "admin" && (
            <Link
              to="/ops/sync"
              className={navClass(pathname === "/ops/sync")}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
              {t("nav.sync")}
              {syncAlerts.length > 0 && (
                <span
                  className={`ml-auto min-w-5 px-1.5 py-0.5 rounded-full text-[10px] font-bold text-center ${urgentSync.length ? "bg-red-600 text-white" : "bg-amber-100 text-amber-800"}`}
                >
                  {syncAlerts.length}
                </span>
              )}
            </Link>
          )}

          {!isReader(user) && (
          <Link
            to="/upload"
            className="flex items-center justify-center gap-2 w-full rounded-lg px-3 py-2.5 mt-2 text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 shadow-sm transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
            </svg>
            {t("nav.uploadDocuments")}
          </Link>
          )}
        </div>
      </nav>

      {urgentSync.length > 0 && (
        <Link
          to="/ops/sync"
          className="mx-3 mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800 space-y-0.5 hover:bg-red-100"
        >
          {urgentSync.map((a) => (
            <p key={a.kind}>⚠ {alertText(a, t)}</p>
          ))}
        </Link>
      )}

      {/* User / logout footer */}
      <div className="px-4 py-3 border-t border-gray-200 space-y-2.5">
        <LanguageSwitcher />
        <div className="flex items-center gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-gray-700 truncate">{user?.full_name ?? user?.email}</p>
            {user?.role && (
              <span className={`inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${user.role === "admin"
                  ? "bg-indigo-100 text-indigo-700"
                  : "bg-gray-100 text-gray-600"
                }`}>
                {t(`roles.${user.role}`)}
              </span>
            )}
            {user?.node_role && (
              <span
                title={user.node_id}
                className={`inline-block mt-0.5 ml-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${user.node_role === "cloud"
                    ? "bg-sky-100 text-sky-700"
                    : "bg-emerald-100 text-emerald-700"
                  }`}>
                {t(user.node_role === "cloud" ? "nav.nodeCloud" : "nav.nodeLocal")}
              </span>
            )}
          </div>
          <button
            onClick={logout}
            className="flex-shrink-0 text-xs text-gray-400 hover:text-red-500 transition-colors"
          >
            {t("nav.signOut")}
          </button>
        </div>
      </div>
    </aside>
  );
}
