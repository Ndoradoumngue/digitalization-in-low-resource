import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { changePassword } from "@sdai/api-client";
import NavSidebar from "../components/NavSidebar";
import { useAuth } from "../context/AuthContext";

const MIN_LENGTH = 8;

// "Changer mon mot de passe" (GED Phase 9). Also the only page reachable
// while the account still has a temporary password (App's gate sends
// every other route here).
export default function ChangePasswordPage() {
  const { t } = useTranslation();
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const forced = !!user?.must_change_password;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < MIN_LENGTH) return setError(t("account.tooShort", { n: MIN_LENGTH }));
    if (next !== confirm) return setError(t("account.mismatch"));
    setBusy(true);
    try {
      await changePassword(current, next);
      await refresh();
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
      if (forced) navigate("/", { replace: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full h-9 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";

  const form = (
    <form onSubmit={submit} className="bg-white rounded-lg border border-gray-200 p-5 space-y-4 max-w-md">
      {forced && (
        <p className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
          {t("account.forcedIntro")}
        </p>
      )}
      <label className="block text-sm">
        <span className="font-medium text-gray-700">{t("account.current")}</span>
        <input type="password" autoComplete="current-password" required value={current}
          onChange={(e) => setCurrent(e.target.value)} className={`${input} mt-1`} />
      </label>
      <div>
        <label className="block text-sm">
          <span className="font-medium text-gray-700">{t("account.new")}</span>
          <input type="password" autoComplete="new-password" required value={next} aria-describedby="new-password-rule"
            onChange={(e) => setNext(e.target.value)} className={`${input} mt-1`} />
        </label>
        <p id="new-password-rule" className="text-xs text-gray-500 mt-0.5">{t("account.rule", { n: MIN_LENGTH })}</p>
      </div>
      <label className="block text-sm">
        <span className="font-medium text-gray-700">{t("account.confirm")}</span>
        <input type="password" autoComplete="new-password" required value={confirm}
          onChange={(e) => setConfirm(e.target.value)} className={`${input} mt-1`} />
      </label>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {done && !forced && <p role="status" className="text-sm text-emerald-700">{t("account.changed")}</p>}
      <div className="flex items-center gap-3">
        <button type="submit" disabled={busy}
          className="h-9 px-4 rounded-md bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50">
          {busy ? t("account.saving") : t("account.save")}
        </button>
        {forced && (
          <button type="button" onClick={logout} className="text-sm text-gray-500 hover:text-red-600">
            {t("nav.signOut")}
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500">{t("account.sessionsNote")}</p>
    </form>
  );

  // No navigation while the temporary password is still in place.
  if (forced) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <h1 className="text-lg font-bold text-gray-900 mb-3">{t("account.title")}</h1>
          {form}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <h1 className="text-lg font-bold text-gray-900 mb-1">{t("account.title")}</h1>
        <p className="text-xs text-gray-500 mb-4">{user?.email}</p>
        {form}
      </main>
    </div>
  );
}
