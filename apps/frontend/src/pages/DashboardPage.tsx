import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { useDbTypes } from "../hooks/useDocumentsDb";
import { useReviewCount } from "../hooks/useReview";
import NavSidebar from "../components/NavSidebar";
import { guideSeen, markGuideSeen } from "../utils/guide";

function StatCard({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-3xl font-bold mt-1 ${accent}`}>{value.toLocaleString()}</p>
    </div>
  );
}

function ActionCard({ to, title, description }: { to: string; title: string; description: string }) {
  return (
    <Link
      to={to}
      className="block rounded-xl border border-gray-200 bg-white p-5 hover:border-indigo-300 hover:shadow-sm transition-all"
    >
      <h3 className="text-sm font-semibold text-indigo-700">{title}</h3>
      <p className="text-sm text-gray-500 mt-1">{description}</p>
    </Link>
  );
}

export default function DashboardPage() {
  const { user }               = useAuth();
  const { t }                  = useTranslation();
  const { data: dbTypes }      = useDbTypes();
  const dbTotal                = (dbTypes ?? []).reduce((acc, t) => acc + t.count, 0);
  const typeCount              = (dbTypes ?? []).length;
  const { data: reviewCount }  = useReviewCount();
  const pendingReview          = reviewCount?.pending ?? 0;
  const [showGuide, setShowGuide] = useState(() => !guideSeen());

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />

      <main className="flex-1 overflow-y-auto p-8">
        <h1 className="text-2xl font-bold text-gray-900">
          {user?.full_name ? t("dashboard.welcomeName", { name: user.full_name }) : t("dashboard.welcome")}
        </h1>
        <p className="text-sm mt-1">
          <Link to="/guide" className="text-indigo-700 hover:underline">
            {t("dashboard.subtitle")}
          </Link>
        </p>

        {showGuide && (
          <div className="mt-6 flex items-start gap-4 rounded-xl border border-indigo-200 bg-indigo-50 p-5">
            <div className="flex-1">
              <h2 className="text-sm font-semibold text-indigo-900">{t("guide.banner.title")}</h2>
              <p className="text-sm text-indigo-800 mt-1">{t("guide.banner.text")}</p>
            </div>
            <Link
              to="/guide"
              className="flex-shrink-0 rounded-lg px-3 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700"
            >
              {t("guide.banner.open")}
            </Link>
            <button
              onClick={() => {
                markGuideSeen();
                setShowGuide(false);
              }}
              className="flex-shrink-0 py-2 text-sm text-indigo-700 hover:text-indigo-900"
            >
              {t("guide.banner.dismiss")}
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-6">
          <StatCard label={t("dashboard.documentsIngested")} value={dbTotal}       accent="text-gray-900" />
          <StatCard label={t("dashboard.pendingReview")}      value={pendingReview} accent={pendingReview > 0 ? "text-amber-600" : "text-gray-900"} />
          <StatCard label={t("dashboard.documentTypes")}      value={typeCount}     accent="text-gray-900" />
        </div>

        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mt-8 mb-3">
          {t("dashboard.quickActions")}
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <ActionCard
            to="/upload"
            title={t("dashboard.uploadTitle")}
            description={t("dashboard.uploadDescription")}
          />
          <ActionCard
            to="/review"
            title={t("dashboard.reviewTitle")}
            description={pendingReview > 0
              ? t("dashboard.reviewDescriptionPending", { count: pendingReview })
              : t("dashboard.reviewDescriptionEmpty")}
          />
          <ActionCard
            to="/documents"
            title={t("dashboard.browseTitle")}
            description={t("dashboard.browseDescription")}
          />
        </div>
      </main>
    </div>
  );
}
