import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useUnreadNotificationCount } from "../hooks/useCollaboration";

// The bell in the sidebar header: unread count (polled every minute),
// opens the notifications page.
export default function NotificationBell() {
  const { t } = useTranslation();
  const { data: count = 0 } = useUnreadNotificationCount();
  return (
    <Link
      to="/notifications"
      className="relative ml-auto p-1.5 rounded-md text-gray-500 hover:text-indigo-700 hover:bg-gray-100"
      aria-label={t("notifications.bellLabel", { count })}
      title={t("notifications.bellLabel", { count })}
    >
      <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" />
      </svg>
      {count > 0 && (
        <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-4 text-center">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}
