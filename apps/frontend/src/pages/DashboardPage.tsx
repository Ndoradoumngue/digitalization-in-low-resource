import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useDbTypes } from "../hooks/useDocumentsDb";
import { useReviewCount } from "../hooks/useReview";
import NavSidebar from "../components/NavSidebar";

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
  const { data: dbTypes }      = useDbTypes();
  const dbTotal                = (dbTypes ?? []).reduce((acc, t) => acc + t.count, 0);
  const typeCount              = (dbTypes ?? []).length;
  const { data: reviewCount }  = useReviewCount();
  const pendingReview          = reviewCount?.pending ?? 0;

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />

      <main className="flex-1 overflow-y-auto p-8">
        <h1 className="text-2xl font-bold text-gray-900">
          Welcome{user?.full_name ? `, ${user.full_name}` : ""}
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Here's what's happening in the document pipeline.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-6">
          <StatCard label="Documents ingested" value={dbTotal}       accent="text-gray-900" />
          <StatCard label="Pending review"      value={pendingReview} accent={pendingReview > 0 ? "text-amber-600" : "text-gray-900"} />
          <StatCard label="Document types"      value={typeCount}     accent="text-gray-900" />
        </div>

        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mt-8 mb-3">
          Quick actions
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <ActionCard
            to="/upload"
            title="Upload documents →"
            description="Ingest new files via upload, a server path, or Google Drive."
          />
          <ActionCard
            to="/review"
            title="Review queue →"
            description={pendingReview > 0
              ? `${pendingReview} document${pendingReview === 1 ? "" : "s"} waiting for review.`
              : "Nothing pending — all caught up."}
          />
          <ActionCard
            to="/documents"
            title="Browse documents →"
            description="Search and filter everything that's been ingested."
          />
        </div>
      </main>
    </div>
  );
}
