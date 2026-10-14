import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { EMAIL_PREFERENCES, type EmailPreference } from "@sdai/types";
import NavSidebar from "../components/NavSidebar";
import {
  useMarkNotificationRead,
  useMyPendingSignatures,
  useNotificationPreferences,
  useNotifications,
  useUpdateNotificationPreferences,
} from "../hooks/useCollaboration";
import { notificationLink, notificationText } from "../utils/notifications";
import { formatDateTime } from "../utils/lifecycle";

// "Notifications": everything addressed to me (newest first), what waits
// for my signature, and how I want to be told by e-mail (GED Phase 7).
export default function NotificationsPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const { data: items, isLoading } = useNotifications(unreadOnly);
  const mark = useMarkNotificationRead();
  const { data: toSign } = useMyPendingSignatures();
  const unread = (items ?? []).filter((n) => !n.read_at).length;

  function open(id: string, link: string, read: boolean) {
    if (!read) mark.mutate(id);
    navigate(link);
  }

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-gray-900">{t("notifications.title")}</h1>
            <div className="flex-1" />
            <label className="flex items-center gap-1.5 text-sm text-gray-600">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
              {t("notifications.unreadOnly")}
            </label>
            <button
              onClick={() => mark.mutate("all")}
              disabled={unread === 0 || mark.isPending}
              className="px-3 py-1.5 rounded-md text-xs font-medium border border-gray-300 hover:bg-gray-50 disabled:opacity-40"
            >
              {t("notifications.markAllRead")}
            </button>
          </div>

          {toSign && toSign.length > 0 && (
            <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <h2 className="text-sm font-semibold text-amber-900 mb-2">
                {t("notifications.toSign", { count: toSign.length })}
              </h2>
              <ul className="space-y-1.5">
                {toSign.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 text-sm">
                    <Link
                      to={`/documents/${r.table_name}/${r.document_id}?tab=signatures`}
                      className="font-medium text-amber-900 hover:underline"
                    >
                      {r.record_id ?? t("notifications.aDocument")}
                    </Link>
                    <span className="text-amber-800">· {r.reason}</span>
                    <span className="ml-auto text-xs text-amber-700">
                      {t("notifications.askedBy", { name: r.requested_by_name ?? "?" })}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="rounded-lg border border-gray-200 bg-white divide-y divide-gray-100">
            {isLoading && <p className="p-4 text-sm text-gray-400">{t("common.loading")}</p>}
            {!isLoading && (items ?? []).length === 0 && (
              <p className="p-6 text-sm text-gray-400 text-center">{t("notifications.empty")}</p>
            )}
            {(items ?? []).map((n) => (
              <div
                key={n.id}
                className={`flex items-start gap-3 px-4 py-3 ${n.read_at ? "" : "bg-indigo-50/50"}`}
              >
                <span
                  className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${n.read_at ? "bg-transparent" : "bg-indigo-500"}`}
                  aria-hidden
                />
                <button
                  onClick={() => open(n.id, notificationLink(n), !!n.read_at)}
                  className="flex-1 text-left min-w-0"
                >
                  <p className={`text-sm ${n.read_at ? "text-gray-600" : "text-gray-900 font-medium"}`}>
                    {notificationText(n, t)}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {t(`notifications.kinds.${n.kind}`, { defaultValue: n.kind })} · {formatDateTime(n.created_at, i18n.language)}
                  </p>
                </button>
                {!n.read_at && (
                  <button
                    onClick={() => mark.mutate(n.id)}
                    className="text-xs text-gray-400 hover:text-indigo-700 flex-shrink-0"
                  >
                    {t("notifications.markRead")}
                  </button>
                )}
              </div>
            ))}
          </section>

          <Preferences />
        </div>
      </main>
    </div>
  );
}

function Preferences() {
  const { t } = useTranslation();
  const { data } = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  if (!data) return null;
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
      <h2 className="text-sm font-semibold text-gray-700">{t("notifications.preferences")}</h2>
      {!data.email_enabled && (
        <p className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">{t("notifications.emailDisabled")}</p>
      )}
      <fieldset className="space-y-1.5">
        <legend className="text-xs text-gray-500 mb-1">{t("notifications.emailLabel")}</legend>
        {EMAIL_PREFERENCES.map((pref: EmailPreference) => (
          <label key={pref} className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="radio"
              name="email_notifications"
              checked={data.email_notifications === pref}
              onChange={() => update.mutate({ email_notifications: pref })}
              disabled={update.isPending}
            />
            {t(`notifications.email.${pref}`)}
          </label>
        ))}
      </fieldset>
      <label className="flex items-center gap-2 text-sm text-gray-700">
        <span className="text-xs text-gray-500">{t("notifications.emailLanguage")}</span>
        <select
          value={data.locale ?? ""}
          onChange={(e) => update.mutate({ locale: e.target.value as "fr" | "en" })}
          className="h-8 rounded-md border border-gray-300 px-2 text-sm"
          disabled={update.isPending}
        >
          {!data.locale && <option value="">—</option>}
          <option value="fr">Français</option>
          <option value="en">English</option>
        </select>
      </label>
      {update.error && <p className="text-xs text-red-600">{(update.error as Error).message}</p>}
    </section>
  );
}
